import asyncio

from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable
import pytest

from state import Candidate


def candidate(name):
    return Candidate(name, f"https://github.com/{name}", "description", 0, "Python", ["agents"])


def test_validator_failure_isolated_and_keeps_shared_checkpoint_open(fake_pipeline, monkeypatch):
    pipeline, state = fake_pipeline
    import nodes.validator as validator
    from nodes.telegram_dispatch import DispatchResult

    items = [candidate("owner/a"), candidate("owner/b"), candidate("owner/c")]
    for item in items:
        item.source_checkpoints = {"agents": "2026-01-02T00:00:00Z"}

    def validate_one(item, _prompt):
        if item.full_name.endswith("/b"):
            raise ResourceExhausted("quota exhausted")
        item.is_accepted = True
        item.validation_reason = "relevant"

    monkeypatch.setattr(validator, "validate_candidate", validate_one)
    monkeypatch.setattr(pipeline, "validate_batch", validator.validate_batch)

    def fetch(candidates):
        for item in candidates:
            if item.is_accepted:
                item.readme = "README evidence"
        return candidates

    def select(candidates, _prompt):
        for item in candidates:
            if item.readme and not item.processing_error:
                item.selector_accepted = True
                item.selector_score = 90
        return candidates

    def explain(candidates):
        for item in candidates:
            if item.is_finalist:
                item.explanation_en = "English"
        return candidates

    def translate(candidates):
        for item in candidates:
            if item.is_finalist and not item.processing_error:
                item.explanation_ka = "Georgian"
        return candidates

    monkeypatch.setattr(pipeline, "fetch_batch", fetch)
    monkeypatch.setattr(pipeline, "select_batch", select)
    monkeypatch.setattr(pipeline, "explain_batch", explain)
    monkeypatch.setattr(pipeline, "translate_batch", translate)

    async def dispatch(sendable):
        return DispatchResult(sent={item.full_name for item in sendable})

    monkeypatch.setattr(pipeline, "dispatch_batch", dispatch)
    asyncio.run(pipeline.run_pipeline(items))

    assert items[0].selector_accepted is True
    assert items[1].processing_error.startswith("validator temporarily failed")
    assert items[2].selector_accepted is True
    assert state.seen == ["owner/a", "owner/c"]
    assert state.checkpoints == [{}]


def test_selector_failure_does_not_stop_later_candidates(monkeypatch):
    import nodes.selector as selector

    items = [candidate("owner/a"), candidate("owner/b"), candidate("owner/c")]
    for item in items:
        item.is_accepted = True
        item.readme = "evidence"

    def select_one(item, _prompt):
        if item.full_name.endswith("/b"):
            raise ServiceUnavailable("Gemini unavailable")
        item.selector_accepted = True
        item.selector_score = 80

    monkeypatch.setattr(selector, "select_candidate", select_one)
    selector.select_batch(items, "taste")

    assert items[0].selector_accepted is True
    assert items[1].processing_error.startswith("selector temporarily failed")
    assert items[2].selector_accepted is True


def test_explainer_failure_does_not_stop_later_finalists(monkeypatch):
    import nodes.explainer as explainer

    items = [candidate("owner/a"), candidate("owner/b"), candidate("owner/c")]
    for item in items:
        item.selector_accepted = True
        item.is_finalist = True

    def explain_one(item):
        if item.full_name.endswith("/b"):
            raise ServiceUnavailable("Gemini unavailable")
        item.explanation_en = "English"

    monkeypatch.setattr(explainer, "explain_repository", explain_one)
    explainer.explain_batch(items)

    assert items[0].explanation_en == "English"
    assert items[1].processing_error.startswith("explainer temporarily failed")
    assert items[2].explanation_en == "English"


def test_translator_failure_does_not_stop_later_finalists(monkeypatch):
    import nodes.translator as translator

    items = [candidate("owner/a"), candidate("owner/b"), candidate("owner/c")]
    for item in items:
        item.is_finalist = True
        item.explanation_en = "English"

    def translate_one(item):
        if item.full_name.endswith("/b"):
            raise ResourceExhausted("quota exhausted")
        item.explanation_ka = "Georgian"

    monkeypatch.setattr(translator, "translate_repository", translate_one)
    translator.translate_batch(items)

    assert items[0].explanation_ka == "Georgian"
    assert items[1].processing_error.startswith("translator temporarily failed")
    assert items[2].explanation_ka == "Georgian"


def test_fetch_failure_does_not_stop_later_candidates(monkeypatch):
    import nodes.github_client as github_client

    items = [candidate("owner/a"), candidate("owner/b"), candidate("owner/c")]
    for item in items:
        item.is_accepted = True

    def get_readme(name):
        if name.endswith("/b"):
            raise ConnectionError("GitHub connection reset")
        return {"is_success": True, "readme": "readme"}

    monkeypatch.setattr(github_client, "get_readme", get_readme)
    monkeypatch.setattr(github_client, "get_tree", lambda _name: [])
    github_client.fetch_batch(items)

    assert items[0].readme == "readme"
    assert items[1].processing_error.startswith("fetcher temporarily failed")
    assert items[2].readme == "readme"


def test_dispatch_failure_isolated_from_other_telegram_sends(monkeypatch):
    import nodes.telegram_dispatch as dispatch

    pending = {}

    class FakePending:
        def prune_old_entries(self):
            return 0
        def add(self, key, value):
            pending[key] = value
        def remove(self, key):
            pending.pop(key, None)

    class FakeBot:
        def __init__(self, token):
            self.session = type("Session", (), {"close": self.close})()
        async def close(self):
            return None
        async def send_message(self, **kwargs):
            if "owner/b" in kwargs["text"]:
                raise ConnectionError("Telegram connection reset")

    items = [candidate("owner/a"), candidate("owner/b"), candidate("owner/c")]
    for item in items:
        item.explanation_ka = "Georgian"
    monkeypatch.setattr(dispatch, "PendingRepos", FakePending)
    monkeypatch.setattr(dispatch, "Bot", FakeBot)
    monkeypatch.setattr(dispatch, "TELEGRAM_TOKEN", "configured")
    monkeypatch.setattr(dispatch, "TELEGRAM_CHAT_ID", "123")
    async def no_sleep(_duration):
        return None
    monkeypatch.setattr(dispatch.asyncio, "sleep", no_sleep)
    result = asyncio.run(dispatch.dispatch_batch(items))

    assert result.sent == {"owner/a", "owner/c"}
    assert set(result.failed) == {"owner/b"}
    assert len(pending) == 2


def test_missing_telegram_configuration_is_run_fatal(monkeypatch):
    import nodes.telegram_dispatch as dispatch

    monkeypatch.setattr(dispatch, "TELEGRAM_TOKEN", None)
    monkeypatch.setattr(dispatch, "TELEGRAM_CHAT_ID", "123")
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        asyncio.run(dispatch.dispatch_batch([candidate("owner/a")]))


def test_missing_github_configuration_is_run_fatal(monkeypatch):
    import nodes.poller as poller

    monkeypatch.setattr(poller, "GITHUB_TOKEN", None)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN is required"):
        poller.run_poller()


def test_google_sdk_internal_retries_are_disabled_behind_bounded_retries():
    import nodes.prompt_engineer as prompt_engineer
    import nodes.validator as validator
    import nodes.selector as selector
    import nodes.explainer as explainer
    import nodes.translator as translator

    assert prompt_engineer.client._api_client._http_options.retry_options.attempts == 1
    assert all(module.llm.max_retries == 1 for module in (validator, selector, explainer, translator))


def test_unclassified_candidate_exception_remains_run_fatal(monkeypatch):
    import nodes.validator as validator

    item = candidate("owner/bug")
    monkeypatch.setattr(
        validator,
        "validate_candidate",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("programming defect")),
    )
    import pytest
    with pytest.raises(RuntimeError, match="programming defect"):
        validator.validate_batch([item], "taste")
