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


def _recommendation_limit() -> int:
    try:
        return max(1, int(os.getenv("MAX_TELEGRAM_RECOMMENDATIONS", "8")))
    except ValueError:
        logger.warning("Invalid MAX_TELEGRAM_RECOMMENDATIONS; using 8")
        return 8


def rank_finalists(candidates: list[Candidate]) -> list[Candidate]:
    """Mark the highest-value selector accepts for the costly writing stages."""
    limit = _recommendation_limit()
    strong_clusters = {"coding_agents", "agents", "python_packages", "prompt_engineering", "llm"}
    accepted = [c for c in candidates if c.selector_accepted is True]

    def rank_key(candidate: Candidate):
        # Selector score carries the evidence and practical-value assessment.
        # Cluster coverage is a small relevance tie-breaker; stars contribute
        # at most one point and never exclude a candidate.
        score = candidate.selector_score if candidate.selector_score is not None else 0
        cluster_bonus = min(3, len(strong_clusters.intersection(candidate.matched_clusters)))
        star_tiebreak = 1 if candidate.stars >= 100 else 0
        return (score + cluster_bonus + star_tiebreak, score, candidate.full_name.casefold())

    accepted.sort(key=rank_key, reverse=True)
    finalists = accepted[:limit]
    finalist_names = {c.full_name for c in finalists}
    for candidate in candidates:
        candidate.is_finalist = candidate.full_name in finalist_names
    return finalists


async def run_pipeline(candidates: list[Candidate]) -> None:
    if not candidates:
        logger.info("[pipeline] no new candidates, stopping")
        return

    try:
        meta_prompt = generate_meta_prompt(PreferenceMemory())
        candidates = validate_batch(candidates, meta_prompt)
        candidates = fetch_batch(candidates)
        candidates = select_batch(candidates, meta_prompt)
        selector_accepts = sum(c.selector_accepted is True for c in candidates)
        finalists = rank_finalists(candidates)
        estimated_calls_saved = 2 * max(0, selector_accepts - len(finalists))
        logger.info(
            "[pipeline] selector accepts=%d finalists=%d cap=%d; avoiding up to %d explainer/translator LLM calls",
            selector_accepts, len(finalists), _recommendation_limit(), estimated_calls_saved,
        )
        candidates = explain_batch(candidates)
        candidates = translate_batch(candidates)
    except Exception:
        logger.exception("[pipeline] candidate processing failed; leaving candidates retryable")
        raise

    sendable = [
        candidate for candidate in candidates
        if candidate.processing_error is None
        and candidate.selector_accepted is True
        and candidate.is_finalist
        and candidate.explanation_en
        and candidate.explanation_ka
    ]
    dispatch_result = await dispatch_batch(sendable)

    for candidate in candidates:
        if candidate.full_name in dispatch_result.failed:
            exc = dispatch_result.failed[candidate.full_name]
            candidate.processing_error = f"telegram dispatch temporarily failed ({type(exc).__name__})"

    completed = set()
    for candidate in candidates:
        if candidate.processing_error:
            continue
        if candidate.is_accepted is False:
            completed.add(candidate.full_name)
        elif candidate.is_accepted is True and candidate.selector_accepted is False:
            # A missing/short README is an intentional fetch-stage rejection.
            completed.add(candidate.full_name)
        elif candidate.is_accepted is True and candidate.selector_accepted is True and not candidate.is_finalist:
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

    normal_rejections = sum(
        candidate.processing_error is None and (
            candidate.is_accepted is False or candidate.selector_accepted is False
        )
        for candidate in candidates
    )
    temporary_failures = sum(candidate.processing_error is not None for candidate in candidates)
    logger.info(
        "[pipeline] run summary: processed=%d rejected=%d temporarily_failed=%d sent=%d",
        len(completed) - normal_rejections,
        normal_rejections,
        temporary_failures,
        len(dispatch_result.sent),
    )
