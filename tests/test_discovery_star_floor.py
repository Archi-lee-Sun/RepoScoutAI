import pytest

from state import Candidate


@pytest.mark.parametrize("stars, expected", [(0, False), (1, False), (2, False), (3, False), (4, False), (5, True), (6, True), (50, True)])
def test_normal_discovery_requires_at_least_five_stars(stars, expected):
    from nodes.poller import _filter_candidates

    item = Candidate("owner/repo", "url", "description", stars, "Python", ["agents"])
    assert (_filter_candidates([item]) == [item]) is expected


def test_normal_discovery_query_has_five_star_qualifier():
    from nodes.poller import build_github_query

    query = build_github_query(["agents"], "2026-01-01T00:00:00Z")
    assert "stars:>=5" in query


@pytest.mark.parametrize("window", ["week", "month"])
def test_trending_queries_and_filters_require_five_stars(monkeypatch, window):
    import nodes.trending_poller as trending

    repos = [
        {"full_name": f"owner/r{stars}", "html_url": f"url{stars}", "description": "repo", "stargazers_count": stars, "language": "Python"}
        for stars in (0, 4, 5, 42)
    ]
    requested = []

    class MemoryState:
        def __init__(self):
            pass
        def get_last_checked(self, _cluster):
            return "2026-01-01T00:00:00Z"
        def is_seen(self, _name):
            return False
        def commit_checkpoints(self, _checkpoints):
            pass

    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"items": repos}

    monkeypatch.setattr(trending, "GITHUB_TOKEN", "configured")
    monkeypatch.setattr(trending, "PollerState", MemoryState)
    monkeypatch.setattr(
        trending.requests,
        "get",
        lambda _url, headers, params, timeout: (requested.append(params["q"]), Response())[1],
    )

    candidates = trending.poll_trending(window)

    assert [item.stars for item in candidates] == [5, 42]
    assert "stars:>=5" in requested[0]
