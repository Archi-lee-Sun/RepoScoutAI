import asyncio
import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

import state as state_module
from state import Candidate, PendingRepos, RepoStatus


def make_candidate(name="owner/repo"):
    candidate = Candidate(name, f"https://github.com/{name}", "description", 12, "Python", ["llm"])
    candidate.is_accepted = True
    candidate.selector_accepted = True
    candidate.explanation_en = "English explanation"
    candidate.explanation_ka = "ქართული ახსნა"
    return candidate


def _add_status(path, repo):
    RepoStatus(str(path)).add_entry(repo, "2026-01-01T00:00:00+00:00", "reason")


def test_pipeline_failure_does_not_mark_seen_or_commit_checkpoint(fake_pipeline, monkeypatch):
    pipeline, state = fake_pipeline
    monkeypatch.setattr(pipeline, "fetch_batch", lambda candidates: (_ for _ in ()).throw(RuntimeError("GitHub timeout")))
    candidate = make_candidate()
    candidate.source_checkpoints = {"llm": "2026-01-01T00:00:00Z"}

    with pytest.raises(RuntimeError, match="GitHub timeout"):
        asyncio.run(pipeline.run_pipeline([candidate]))

    assert state.seen == []
    assert state.checkpoints == []


def test_normal_selector_rejection_is_seen(fake_pipeline, monkeypatch):
    pipeline, state = fake_pipeline
    candidate = make_candidate()
    candidate.selector_accepted = False
    candidate.source_checkpoints = {"llm": "2026-01-01T00:00:00Z"}
    from nodes.telegram_dispatch import DispatchResult
    monkeypatch.setattr(pipeline, "dispatch_batch", _async_value(DispatchResult()))

    asyncio.run(pipeline.run_pipeline([candidate]))

    assert state.seen == [candidate.full_name]
    assert state.checkpoints == [{"llm": "2026-01-01T00:00:00Z"}]


def test_telegram_failure_is_visible_and_remains_retryable(fake_pipeline, monkeypatch):
    pipeline, state = fake_pipeline
    candidate = make_candidate()
    candidate.source_checkpoints = {"llm": "2026-01-01T00:00:00Z"}
    from nodes.telegram_dispatch import DispatchResult
    result = DispatchResult(failed={candidate.full_name: OSError("Telegram unavailable")})
    monkeypatch.setattr(pipeline, "dispatch_batch", _async_value(result))

    asyncio.run(pipeline.run_pipeline([candidate]))

    assert state.seen == []
    assert state.checkpoints == [{}]


def test_telegram_success_marks_seen_and_commits_checkpoint(fake_pipeline, monkeypatch):
    pipeline, state = fake_pipeline
    candidate = make_candidate()
    candidate.source_checkpoints = {"llm": "2026-01-01T00:00:00Z"}
    from nodes.telegram_dispatch import DispatchResult
    monkeypatch.setattr(
        pipeline,
        "dispatch_batch",
        _async_value(DispatchResult(sent={candidate.full_name})),
    )

    asyncio.run(pipeline.run_pipeline([candidate]))

    assert state.seen == [candidate.full_name]
    assert state.checkpoints == [{"llm": "2026-01-01T00:00:00Z"}]


def test_partial_telegram_failure_completes_only_successful_candidates(fake_pipeline, monkeypatch):
    pipeline, state = fake_pipeline
    first = make_candidate("owner/first")
    second = make_candidate("owner/second")
    first.source_checkpoints = {"llm": "2026-01-01T00:00:00Z"}
    second.source_checkpoints = {"llm": "2026-01-01T00:00:00Z"}
    from nodes.telegram_dispatch import DispatchResult
    result = DispatchResult(
        sent={first.full_name},
        failed={second.full_name: OSError("Telegram timeout")},
    )
    monkeypatch.setattr(pipeline, "dispatch_batch", _async_value(result))

    asyncio.run(pipeline.run_pipeline([first, second]))

    assert state.seen == [first.full_name]
    assert state.checkpoints == [{}]


def test_dispatch_saves_pending_before_send_and_removes_it_after_send_failure(monkeypatch):
    import nodes.telegram_dispatch as dispatch

    store = {}

    class FakePending:
        def prune_old_entries(self):
            return 0
        def add(self, key, value):
            store[key] = value
        def remove(self, key):
            store.pop(key, None)

    class FakeBot:
        def __init__(self, token):
            self.session = SimpleNamespace(close=self.close)
        async def close(self):
            return None
        async def send_message(self, **kwargs):
            assert len(store) == 1
            raise ConnectionError("send failed")

    monkeypatch.setattr(dispatch, "PendingRepos", FakePending)
    monkeypatch.setattr(dispatch, "Bot", FakeBot)
    monkeypatch.setattr(dispatch, "TELEGRAM_TOKEN", "configured")
    monkeypatch.setattr(dispatch, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(dispatch.asyncio, "sleep", _no_sleep)

    result = asyncio.run(dispatch.dispatch_batch([make_candidate()]))

    assert not result.sent
    assert "owner/repo" in result.failed
    assert store == {}


async def _no_sleep(_duration):
    return None


def test_dispatch_success_leaves_callback_data_pending(monkeypatch):
    import nodes.telegram_dispatch as dispatch

    store = {}

    class FakePending:
        def prune_old_entries(self):
            return 0
        def add(self, key, value):
            store[key] = value
        def remove(self, key):
            store.pop(key, None)

    class FakeBot:
        def __init__(self, token):
            self.session = SimpleNamespace(close=self.close)
        async def close(self):
            return None
        async def send_message(self, **kwargs):
            assert len(store) == 1
            self.message = kwargs

    monkeypatch.setattr(dispatch, "PendingRepos", FakePending)
    monkeypatch.setattr(dispatch, "Bot", FakeBot)
    monkeypatch.setattr(dispatch, "TELEGRAM_TOKEN", "configured")
    monkeypatch.setattr(dispatch, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(dispatch.asyncio, "sleep", _no_sleep)

    result = asyncio.run(dispatch.dispatch_batch([make_candidate()]))

    assert result.sent == {"owner/repo"}
    assert len(store) == 1
    pending = next(iter(store.values()))
    assert pending["full_name"] == "owner/repo"


def test_pending_pop_allows_only_one_duplicate_callback(tmp_path, monkeypatch):
    monkeypatch.setattr(state_module, "PENDING_FILE", tmp_path / "pending.json")
    pending = PendingRepos()
    pending.add("callback", {"full_name": "owner/repo"})

    assert pending.pop("callback")["full_name"] == "owner/repo"
    assert pending.pop("callback") is None


def test_pending_claim_rejects_duplicate_then_recovers_stale_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(state_module, "PENDING_FILE", tmp_path / "pending.json")
    pending = PendingRepos()
    pending.add("callback", {"full_name": "owner/repo"})

    assert pending.claim("callback") is not None
    assert pending.claim("callback") is None
    entry = pending.get("callback")
    entry["claimed_at"] = "2000-01-01T00:00:00+00:00"
    pending.save_atomic({"callback": entry})
    assert pending.claim("callback") is not None


def test_duplicate_accept_callback_stars_and_records_once(monkeypatch):
    import nodes.bot as bot

    pending_calls = {"claim": 0, "remove": 0}
    history_calls = []
    status_calls = []
    star_calls = []

    class FakePending:
        def claim(self, _repo_id):
            pending_calls["claim"] += 1
            if pending_calls["claim"] == 1:
                return {"full_name": "owner/repo", "url": "url", "description": "desc"}
            return None
        def remove(self, _repo_id):
            pending_calls["remove"] += 1

    class FakeHistory:
        def add_decision(self, *args, **kwargs):
            history_calls.append((args, kwargs))

    class FakeStatus:
        def add_entry(self, **kwargs):
            status_calls.append(kwargs)

    async def fake_star(_name):
        star_calls.append(_name)
        return True

    monkeypatch.setattr(bot, "PendingRepos", FakePending)
    monkeypatch.setattr(bot, "PreferenceMemory", FakeHistory)
    monkeypatch.setattr(bot, "RepoStatus", FakeStatus)
    monkeypatch.setattr(bot, "star_repo", lambda name: True)
    monkeypatch.setattr(bot.asyncio, "to_thread", lambda fn, name: fake_star(name))

    answers = []
    edits = []
    async def answer(*args, **kwargs):
        _record(answers, (args, kwargs))
    async def edit_text(value):
        _record(edits, value)
    callback = SimpleNamespace(
        data="a:callback",
        message=SimpleNamespace(edit_text=edit_text),
        answer=answer,
    )
    asyncio.run(bot.handle_decision(callback))
    asyncio.run(bot.handle_decision(callback))

    assert pending_calls["remove"] == 1
    assert len(history_calls) == 1
    assert history_calls[0][1]["decision_id"] == "callback"
    assert star_calls == ["owner/repo"]
    assert len(status_calls) == 1
    assert answers[-1][0][0] == "Already handled or processing"


def _record(target, value):
    target.append(value)


def test_failed_github_star_releases_callback_for_retry(monkeypatch):
    import nodes.bot as bot

    calls = []

    class FakePending:
        def claim(self, _repo_id):
            return {"full_name": "owner/repo", "url": "url", "description": "desc"}
        def release(self, repo_id):
            calls.append(("release", repo_id))
        def remove(self, repo_id):
            calls.append(("remove", repo_id))

    class FakeHistory:
        def add_decision(self, *args, **kwargs):
            calls.append(("history", args, kwargs))

    async def fake_to_thread(fn, name):
        return False

    monkeypatch.setattr(bot, "PendingRepos", FakePending)
    monkeypatch.setattr(bot, "PreferenceMemory", FakeHistory)
    monkeypatch.setattr(bot, "star_repo", lambda name: False)
    monkeypatch.setattr(bot.asyncio, "to_thread", fake_to_thread)
    answers = []
    async def answer(*args, **kwargs):
        _record(answers, (args, kwargs))
    callback = SimpleNamespace(
        data="a:callback",
        message=SimpleNamespace(edit_text=lambda *_: None),
        answer=answer,
    )

    asyncio.run(bot.handle_decision(callback))

    assert calls == [("release", "callback")]
    assert "retry" in answers[0][0][0]


def test_stale_callback_has_no_side_effects(monkeypatch):
    import nodes.bot as bot

    class FakePending:
        def claim(self, _repo_id):
            return None

    class Forbidden:
        def __init__(self):
            raise AssertionError("stale callback must not touch persistent side effects")

    monkeypatch.setattr(bot, "PendingRepos", FakePending)
    monkeypatch.setattr(bot, "PreferenceMemory", Forbidden)
    answers = []
    async def answer(*args, **kwargs):
        answers.append((args, kwargs))
    callback = SimpleNamespace(
        data="r:expired",
        message=SimpleNamespace(edit_text=_async_value(None)),
        answer=answer,
    )

    asyncio.run(bot.handle_decision(callback))

    assert answers[0][0] == ("Already handled or processing",)


def test_reject_callback_records_once_without_github_call(monkeypatch):
    import nodes.bot as bot

    calls = []
    class FakePending:
        def claim(self, _repo_id):
            return {"full_name": "owner/repo", "url": "url", "description": "desc"}
        def remove(self, repo_id):
            calls.append(("remove", repo_id))
    class FakeHistory:
        def add_decision(self, *args, **kwargs):
            calls.append(("history", args, kwargs))
    def forbidden_star(_name):
        raise AssertionError("reject must not star")
    async def answer(*args, **kwargs):
        calls.append(("answer", args, kwargs))
    async def edit_text(value):
        calls.append(("edit", value))

    monkeypatch.setattr(bot, "PendingRepos", FakePending)
    monkeypatch.setattr(bot, "PreferenceMemory", FakeHistory)
    monkeypatch.setattr(bot, "star_repo", forbidden_star)
    callback = SimpleNamespace(
        data="r:callback", message=SimpleNamespace(edit_text=edit_text), answer=answer
    )
    asyncio.run(bot.handle_decision(callback))

    assert calls[0][0] == "history"
    assert calls[0][2]["decision_id"] == "callback"
    assert ("remove", "callback") in calls


def _async_value(value):
    async def inner(*_args, **_kwargs):
        return value
    return inner


def test_bot_main_starts_long_polling(monkeypatch):
    import nodes.bot as bot

    started = []
    class FakeDispatcher:
        async def start_polling(self, active_bot):
            started.append(active_bot)
    active_bot = object()
    monkeypatch.setattr(bot, "dp", FakeDispatcher())
    monkeypatch.setattr(bot, "bot", active_bot)

    asyncio.run(bot.main())

    assert started == [active_bot]


def test_cli_routes_all_supported_jobs(monkeypatch):
    import main

    routed = []
    candidates = [make_candidate()]
    monkeypatch.setattr(main, "run_poller", lambda: (routed.append("discover"), candidates)[1])
    monkeypatch.setattr(main, "poll_trending", lambda window: (routed.append(window), candidates)[1])
    monkeypatch.setattr(main, "run_cleanup", lambda: routed.append("cleanup"))
    async def pipeline_run(batch):
        routed.append(("pipeline", batch))
    monkeypatch.setattr(main, "run_pipeline", pipeline_run)
    import sys

    for job in ("discover", "trending-week", "trending-month", "cleanup"):
        monkeypatch.setattr(sys, "argv", ["main.py", job])
        asyncio.run(main.main())

    assert routed == [
        "discover", ("pipeline", candidates),
        "week", ("pipeline", candidates),
        "month", ("pipeline", candidates),
        "cleanup",
    ]


def test_explanation_and_translation_errors_leave_candidates_retryable(monkeypatch):
    import nodes.explainer as explainer
    import nodes.translator as translator
    from google.api_core.exceptions import ServiceUnavailable

    candidate = make_candidate()
    candidate.is_finalist = True
    candidate.explanation_ka = None
    monkeypatch.setattr(
        explainer,
        "explain_repository",
        lambda _: (_ for _ in ()).throw(ServiceUnavailable("LLM unavailable")),
    )
    explainer.explain_batch([candidate])
    assert candidate.processing_error.startswith("explainer temporarily failed")

    candidate.processing_error = None
    candidate.explanation_en = "English"
    monkeypatch.setattr(
        translator,
        "translate_repository",
        lambda _: (_ for _ in ()).throw(ServiceUnavailable("LLM unavailable")),
    )
    translator.translate_batch([candidate])
    assert candidate.processing_error.startswith("translator temporarily failed")


def test_pending_expiry_prunes_stale_callback(tmp_path, monkeypatch):
    monkeypatch.setattr(state_module, "PENDING_FILE", tmp_path / "pending.json")
    pending = PendingRepos()
    pending.add("old", {"full_name": "owner/repo"})
    entry = pending.get("old")
    entry["created_at"] = "2000-01-01T00:00:00+00:00"
    pending.save_atomic({"old": entry})

    assert pending.prune_old_entries(max_days=30) == 1
    assert pending.get("old") is None


def test_repo_status_updates_from_separate_processes_are_not_lost(tmp_path):
    path = tmp_path / "repo_status.json"
    ctx = multiprocessing.get_context("spawn")
    processes = [
        ctx.Process(target=_add_status, args=(path, f"owner/repo-{number}"))
        for number in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0

    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"owner/repo-0", "owner/repo-1"}


def test_atomic_write_failure_preserves_original_json(tmp_path, monkeypatch):
    path = tmp_path / "repo_status.json"
    RepoStatus(str(path)).add_entry("owner/original", "time", "reason")

    def fail_replace(source, destination):
        raise OSError("simulated interruption before replace")

    monkeypatch.setattr(state_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        RepoStatus(str(path)).add_entry("owner/new", "time", "reason")

    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"owner/original"}
    assert not path.with_suffix(".json.tmp").exists()


def test_get_readme_network_failure_is_retryable(monkeypatch):
    import nodes.github_client as github
    import requests

    def fail(*args, **kwargs):
        raise requests.Timeout("temporary timeout")
    monkeypatch.setattr(github.requests, "get", fail)

    with pytest.raises(requests.Timeout):
        github.get_readme("owner/repo")


def test_poller_defers_candidate_cluster_checkpoint_until_pipeline(monkeypatch):
    import nodes.poller as poller

    class MemoryState:
        committed = []
        def __init__(self):
            self.values = {}
        def get_last_checked(self, cluster):
            return self.values.get(cluster)
        def is_seen(self, name):
            return False
        def commit_checkpoints(self, checkpoints):
            self.committed.append(dict(checkpoints))

    MemoryState.committed = []
    monkeypatch.setattr(poller, "GITHUB_TOKEN", "configured")
    monkeypatch.setattr(poller, "PollerState", MemoryState)
    monkeypatch.setattr(poller, "INTERESTS", {"llm": ["llm"], "agents": ["agent"]})
    def fetch(query, page, per_page, headers):
        if "llm" in query:
            if page == 1:
                return [{
                    "html_url": "https://github.com/owner/repo",
                    "full_name": "owner/repo",
                    "description": "desc",
                    "stargazers_count": 5,
                    "language": "Python",
                }]
        return []
    monkeypatch.setattr(poller, "_fetch_page", fetch)

    candidates = poller.run_poller()

    assert len(candidates) == 1
    assert "llm" in candidates[0].source_checkpoints
    assert MemoryState.committed and all("llm" not in call for call in MemoryState.committed)
    assert {"agents"} <= set(MemoryState.committed[0])


def test_poller_partial_page_failure_does_not_advance_cluster(monkeypatch):
    import nodes.poller as poller

    class MemoryState:
        committed = []
        def __init__(self):
            pass
        def get_last_checked(self, _cluster):
            return None
        def is_seen(self, _name):
            return False
        def commit_checkpoints(self, checkpoints):
            self.committed.append(dict(checkpoints))

    MemoryState.committed = []
    monkeypatch.setattr(poller, "GITHUB_TOKEN", "configured")
    monkeypatch.setattr(poller, "PollerState", MemoryState)
    monkeypatch.setattr(poller, "INTERESTS", {"llm": ["llm"]})
    items = [
        {
            "html_url": f"https://github.com/owner/repo-{index}",
            "full_name": f"owner/repo-{index}",
            "description": "desc",
            "stargazers_count": 5,
            "language": "Python",
        }
        for index in range(100)
    ]
    monkeypatch.setattr(
        poller,
        "_fetch_page",
        lambda query, page, per_page, headers: items if page == 1 else None,
    )

    candidates = poller.run_poller()

    assert len(candidates) == 100
    assert all("llm" not in candidate.source_checkpoints for candidate in candidates)
    assert MemoryState.committed == []


def test_trending_uses_checkpoint_to_keep_older_failures_retryable(monkeypatch):
    import nodes.trending_poller as trending

    class MemoryState:
        committed = []
        def __init__(self):
            pass
        def get_last_checked(self, cluster):
            assert cluster == "trending_week"
            return "2020-01-01T00:00:00Z"
        def is_seen(self, _name):
            return False
        def commit_checkpoints(self, checkpoints):
            self.committed.append(dict(checkpoints))

    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return {"items": [{
                "full_name": "owner/old-repo",
                "html_url": "https://github.com/owner/old-repo",
                "description": "still retryable",
                "stargazers_count": 20,
                "language": "Python",
            }]}

    MemoryState.committed = []
    monkeypatch.setattr(trending, "GITHUB_TOKEN", "configured")
    requested = []
    monkeypatch.setattr(trending, "PollerState", MemoryState)
    monkeypatch.setattr(
        trending.requests,
        "get",
        lambda url, headers, params, timeout: (requested.append(params["q"]), Response())[1],
    )

    candidates = trending.poll_trending("week")

    assert len(candidates) == 1
    assert candidates[0].source_checkpoints["trending_week"].startswith("20")
    assert "created:>2020-01-01T00:00:00Z" in requested[0]
    assert "stars:>=5" in requested[0]
    assert MemoryState.committed == []


def test_failed_cleanup_unstar_remains_retryable(monkeypatch):
    import nodes.cleanup as cleanup

    status_updates = []
    status = SimpleNamespace(update_entry=lambda *args, **kwargs: status_updates.append(args))
    monkeypatch.setattr(cleanup, "get_repo_info", lambda _: {"pushed_at": "2020-01-01T00:00:00Z"})
    monkeypatch.setattr(cleanup, "get_readme", lambda _: {"readme": "readme"})
    monkeypatch.setattr(cleanup, "get_tree", lambda _: [])
    monkeypatch.setattr(cleanup, "format_tree_text", lambda _: "tree")
    monkeypatch.setattr(cleanup, "pick_files", lambda _: [])
    monkeypatch.setattr(cleanup, "structured_llm", SimpleNamespace(
        invoke=lambda _: SimpleNamespace(functional=False)
    ))
    monkeypatch.setattr(cleanup, "unstar_repo", lambda _: False)

    cleanup.check_repo("owner/repo", {"functional": None}, status)

    assert status_updates == []


def test_successful_cleanup_unstar_is_recorded(monkeypatch):
    import nodes.cleanup as cleanup

    updates = []
    status = SimpleNamespace(update_entry=lambda *args, **kwargs: updates.append((args, kwargs)))
    monkeypatch.setattr(cleanup, "get_repo_info", lambda _: {"pushed_at": "2020-01-01T00:00:00Z"})
    monkeypatch.setattr(cleanup, "get_readme", lambda _: {"readme": "readme"})
    monkeypatch.setattr(cleanup, "get_tree", lambda _: [])
    monkeypatch.setattr(cleanup, "format_tree_text", lambda _: "tree")
    monkeypatch.setattr(cleanup, "pick_files", lambda _: [])
    monkeypatch.setattr(cleanup, "structured_llm", SimpleNamespace(
        invoke=lambda _: SimpleNamespace(functional=False)
    ))
    monkeypatch.setattr(cleanup, "unstar_repo", lambda _: True)

    cleanup.check_repo("owner/repo", {"functional": None}, status)

    assert updates and updates[0][0] == ("owner/repo",)
    assert updates[0][1]["functional"] is False
