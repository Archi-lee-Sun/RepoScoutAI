import sys
import os
ROOT_DIR = os.path.dirname(__file__)
NODES_DIR = os.path.join(ROOT_DIR, "nodes")
for path in (ROOT_DIR, NODES_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import logging

from nodes.prompt_engineer import generate_meta_prompt
from nodes.validator import validate_batch
from nodes.github_client import fetch_batch
from nodes.selector import select_batch
from nodes.explainer import explain_batch
from nodes.translator import translate_batch
from nodes.telegram_dispatch import dispatch_batch
from state import Candidate, PreferenceMemory, PollerState

logger = logging.getLogger(__name__)


class DispatchBatchError(RuntimeError):
    def __init__(self, failed: dict[str, Exception]):
        self.failed = failed
        details = "; ".join(f"{repo}: {error}" for repo, error in failed.items())
        super().__init__(f"Telegram delivery failed for {len(failed)} candidate(s): {details}")


async def run_pipeline(candidates: list[Candidate]) -> None:
    if not candidates:
        logger.info("[pipeline] no new candidates, stopping")
        return

    meta_prompt = generate_meta_prompt(PreferenceMemory())

    try:
        candidates = validate_batch(candidates, meta_prompt)
        candidates = fetch_batch(candidates)
        candidates = select_batch(candidates, meta_prompt)
        candidates = explain_batch(candidates)
        candidates = translate_batch(candidates)
    except Exception:
        logger.exception("[pipeline] candidate processing failed; leaving candidates retryable")
        raise

    sendable = [
        candidate for candidate in candidates
        if candidate.processing_error is None
        and candidate.selector_accepted is True
        and candidate.explanation_en
        and candidate.explanation_ka
    ]
    dispatch_result = await dispatch_batch(sendable)

    completed = set()
    for candidate in candidates:
        if candidate.processing_error:
            continue
        if candidate.is_accepted is False:
            completed.add(candidate.full_name)
        elif candidate.is_accepted is True and candidate.selector_accepted is False:
            # A missing/short README is an intentional fetch-stage rejection.
            completed.add(candidate.full_name)
        elif candidate.is_accepted is True and candidate.selector_accepted is True:
            if candidate.full_name in dispatch_result.sent:
                completed.add(candidate.full_name)

    poller_state = PollerState()
    poller_state.mark_seen_batch(sorted(completed))

    # Commit each search checkpoint only when every candidate attributable to
    # that cluster completed. A failed candidate keeps just its source cluster
    # open, while completed candidates remain deduplicated through the seen set.
    cluster_repos: dict[str, set[str]] = {}
    cluster_targets: dict[str, str] = {}
    for candidate in candidates:
        for cluster, timestamp in candidate.source_checkpoints.items():
            cluster_repos.setdefault(cluster, set()).add(candidate.full_name)
            cluster_targets[cluster] = timestamp
    committable = {
        cluster: timestamp
        for cluster, timestamp in cluster_targets.items()
        if cluster_repos[cluster].issubset(completed)
    }
    poller_state.commit_checkpoints(committable)

    if dispatch_result.failed:
        raise DispatchBatchError(dispatch_result.failed)

    incomplete = {
        candidate.full_name: candidate.processing_error
        for candidate in candidates
        if candidate.processing_error
    }
    if incomplete:
        logger.error("[pipeline] candidates left retryable after stage failures: %s", incomplete)
