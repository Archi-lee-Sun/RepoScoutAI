"""
nodes/poller.py

Candidate Finder agent: queries the GitHub Search API for new repositories
matching each interest cluster below, dedupes results across clusters,
tracks per-cluster "last checked" state, filters out low-signal results,
and returns the merged candidate list for the next step (validator).
"""

import os
import sys
import logging
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from state import Candidate, PollerState

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
STARS_NUMBER = 3

INTERESTS = {
    "llm": ["llm", "language model", "gpt", "gemini"],
    "agents": ["agent", "multi-agent", "agentic", "orchestration"],
    "python_packages": ["python library", "python package"],
    "app_dev": ["mobile app", "app development", "flutter", "react native"],
    "ux_design": ["ux design", "ui design", "design system"],
    "coding_agents": ["codex", "claude code", "antigravity", "copilot"],
    "prompt_engineering": ["prompt engineering", "prompting", "system prompt"],
}


def build_github_query(keywords: list[str], last_checked: str | None) -> str:
    keywords_query = " OR ".join(
        f'"{kw}"' if " " in kw else kw for kw in keywords
    )

    if not last_checked:
        default_time = datetime.now(timezone.utc) - timedelta(days=1)
        last_checked = default_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    return f"({keywords_query}) stars:>{STARS_NUMBER} created:>{last_checked}"


def _process_items(items: list, interest: str, unique_candidates: dict, poller_state: PollerState) -> None:
    for item in items:
        url = item["html_url"]
        full_name = item["full_name"]

        if poller_state.is_seen(full_name):
            continue

        if url in unique_candidates:
            if interest not in unique_candidates[url].matched_clusters:
                unique_candidates[url].matched_clusters.append(interest)
        else:
            unique_candidates[url] = Candidate(
                full_name=full_name,
                url=url,
                description=item.get("description") or "",
                stars=item["stargazers_count"],
                language=item.get("language") or "",
                matched_clusters=[interest],
            )


def _fetch_page(query: str, page: int, per_page: int, headers: dict) -> list | None:
    params = {
        "q": query,
        "sort": "created",
        "order": "desc",
        "per_page": per_page,
        "page": page,
    }
    try:
        response = requests.get(GITHUB_SEARCH_URL, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        return response.json().get("items", [])
    except requests.exceptions.RequestException as e:
        logging.warning(f"[ERROR] request failed (page {page}): {e}")
        return None


def _filter_candidates(candidates: list) -> list:
    """
    Drops candidates missing a description, and repos at or below a
    2-star floor. Note: the star check is currently redundant with the
    stars:>{STARS_NUMBER} qualifier already in the GitHub query itself —
    kept here as a separate safety net in case that query-level
    threshold changes later. The description check is the one doing
    real work: GitHub's search API has no "has description" qualifier,
    so this can only be filtered post-fetch, in Python.
    """
    return [c for c in candidates if c.description and c.stars > 2]


def run_poller() -> list[Candidate]:
    poller_state = PollerState()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "RepoScoutAI-App",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
    }

    unique_candidates: dict[str, Candidate] = {}
    current_run_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    successful_checkpoints: dict[str, str] = {}

    for interest, keywords in INTERESTS.items():
        last_checked = poller_state.get_last_checked(interest)
        github_query = build_github_query(keywords, last_checked)

        interest_succeeded = True
        page = 1
        reached_page_limit = False
        continuation_checkpoint = None

        while True:
            per_page = 100
            items = _fetch_page(github_query, page, per_page, headers)
            if items is None:
                logging.warning(
                    f"[ERROR] შეცდომა {interest} კლასტერის ძებნისას (page {page}) — "
                    f"skipping checkpoint update for this interest"
                )
                interest_succeeded = False
                break
            if not items:
                break
            _process_items(items, interest, unique_candidates, poller_state)
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

        if interest_succeeded and reached_page_limit and continuation_checkpoint:
            successful_checkpoints[interest] = continuation_checkpoint
            logging.warning(
                "[poller] reached GitHub's 1,000-result search limit for %s; "
                "will continue from %s after this batch completes",
                interest,
                continuation_checkpoint,
            )
        elif interest_succeeded and not reached_page_limit:
            successful_checkpoints[interest] = current_run_time
        elif reached_page_limit:
            logging.warning(
                "[poller] reached the search limit for %s without a usable continuation "
                "timestamp; leaving its checkpoint unchanged",
                interest,
            )

    candidates = list(unique_candidates.values())
    candidates = _filter_candidates(candidates)

    # A checkpoint with candidates is committed only after pipeline completion.
    # Empty clusters (or results removed by intentional local filters) are safe
    # to advance immediately because they have no downstream work to lose.
    cluster_candidates: dict[str, list[Candidate]] = {name: [] for name in successful_checkpoints}
    for candidate in candidates:
        for cluster in candidate.matched_clusters:
            if cluster in cluster_candidates:
                cluster_candidates[cluster].append(candidate)

    empty_clusters = {
        cluster: timestamp
        for cluster, timestamp in successful_checkpoints.items()
        if not cluster_candidates[cluster]
    }
    if empty_clusters:
        poller_state.commit_checkpoints(empty_clusters)

    for candidate in candidates:
        candidate.source_checkpoints = {
            cluster: successful_checkpoints[cluster]
            for cluster in candidate.matched_clusters
            if cluster in successful_checkpoints
        }

    logging.info(f"[poller] {len(candidates)} candidate repos after filtering")
    return candidates


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_poller()
