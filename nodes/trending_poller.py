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


def poll_trending(window: str = "week") -> list[Candidate]:
    """
    Queries GitHub's Search API for trending repositories created within the given window
    (7 days back for 'week', 30 days back for 'month'), sorted by stars.
    Deduplicates against previously seen repos using PollerState.
    """
    days = 7 if window == "week" else 30
    cluster_name = f"trending_{window}"

    cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_iso = cutoff_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    query = f"created:>={cutoff_iso} stars:>2"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "RepoScoutAI-App",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
    }

    poller_state = PollerState()
    candidates: list[Candidate] = []
    seen_in_run: set[str] = set()

    for page, per_page in [(1, 100), (2, 50)]:
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
            logger.warning(f"[trending_poller] request failed (page {page}): {e}")
            continue

        for item in items:
            full_name = item.get("full_name")
            if not full_name or full_name in seen_in_run:
                continue

            # Skip repos that were already discovered/seen across any pipeline source
            if poller_state.is_seen(full_name):
                continue

            description = item.get("description") or ""
            stars = item.get("stargazers_count", 0)

            # Filter out candidates with empty descriptions or <= 2 stars (consistent with poller.py)
            if not description or stars <= 2:
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

    if candidates:
        poller_state.mark_seen_batch([c.full_name for c in candidates])

    logger.info(f"[trending_poller] {len(candidates)} new trending candidate repos found for window '{window}'")
    return candidates
