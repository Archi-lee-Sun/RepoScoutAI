import os
import sys
import logging
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from state import Candidate, PollerState

load_dotenv()
logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
MIN_STARS = 5


def poll_trending(window: str = "week") -> list[Candidate]:
    """
    Queries GitHub's Search API for trending repositories created within the given window
    (7 days back for 'week', 30 days back for 'month'), sorted by stars.
    Deduplicates against previously seen repos using PollerState.
    """
    if window not in {"week", "month"}:
        raise ValueError("window must be 'week' or 'month'")
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is required for trending discovery")
    days = 7 if window == "week" else 30
    cluster_name = f"trending_{window}"

    poller_state = PollerState()
    current_run_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    last_checked = poller_state.get_last_checked(cluster_name)
    if last_checked:
        cutoff_iso = last_checked
    else:
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_iso = cutoff_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    query = f"created:>{cutoff_iso} stars:>={MIN_STARS}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "RepoScoutAI-App",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
    }

    candidates: list[Candidate] = []
    seen_in_run: set[str] = set()

    page = 1
    query_succeeded = True
    continuation_checkpoint = None
    reached_page_limit = False
    while True:
        per_page = 100
        params = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": per_page,
            "page": page,
        }

        try:
            response = requests.get(
                GITHUB_SEARCH_URL,
                headers=headers,
                params=params,
                timeout=20,
            )
            response.raise_for_status()
            items = response.json().get("items", [])
        except requests.exceptions.RequestException as e:
            if getattr(getattr(e, "response", None), "status_code", None) == 401:
                raise RuntimeError("GITHUB_TOKEN was rejected by GitHub") from e
            logger.warning(f"[trending_poller] request failed (page {page}): {e}")
            query_succeeded = False
            break

        if not items:
            break

        for item in items:
            full_name = item.get("full_name")
            if not full_name or full_name in seen_in_run:
                continue

            # Skip repos that were already discovered/seen across any pipeline source
            if poller_state.is_seen(full_name):
                continue

            description = item.get("description") or ""
            stars = item.get("stargazers_count", 0)

            # Enforce the same minimum locally even though the query filters too.
            if not description or stars < MIN_STARS:
                continue

            seen_in_run.add(full_name)
            candidates.append(
                Candidate(
                    full_name=full_name,
                    url=item.get("html_url", f"https://github.com/{full_name}"),
                    description=description,
                    stars=stars,
                    language=item.get("language") or "",
                    matched_clusters=[cluster_name],
                )
            )

        if len(items) < per_page:
            break
        if page >= 10:
            reached_page_limit = True
            oldest_created = items[-1].get("created_at")
            if oldest_created:
                oldest_time = datetime.fromisoformat(oldest_created.replace("Z", "+00:00"))
                continuation_checkpoint = (
                    oldest_time - timedelta(seconds=1)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            break
        page += 1

    if reached_page_limit and not continuation_checkpoint:
        query_succeeded = False
        logger.warning(
            "[trending_poller] reached the result limit without a continuation timestamp; "
            "leaving %s checkpoint unchanged",
            cluster_name,
        )

    if query_succeeded:
        checkpoint = continuation_checkpoint or current_run_time
        if candidates:
            for candidate in candidates:
                candidate.source_checkpoints[cluster_name] = checkpoint
        else:
            poller_state.commit_checkpoints({cluster_name: checkpoint})

    logger.info(f"[trending_poller] {len(candidates)} new trending candidate repos found for window '{window}'")
    return candidates
