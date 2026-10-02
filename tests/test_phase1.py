import asyncio

import pytest
from state import Candidate


def test_pipeline_leaves_candidates_retryable_on_stage_error(fake_pipeline, monkeypatch):
    pipeline, state = fake_pipeline
    monkeypatch.setattr(pipeline, "fetch_batch", lambda candidates: (_ for _ in ()).throw(RuntimeError("fetch failed")))
    candidate = Candidate(
        "owner/repo", "https://github.com/owner/repo", "description", 3, "Python", ["llm"],
        source_checkpoints={"llm": "2026-01-01T00:00:00Z"},
    )

    with pytest.raises(RuntimeError, match="fetch failed"):
        asyncio.run(pipeline.run_pipeline([candidate]))

    assert state.seen == []
    assert state.checkpoints == []


def test_validator_rejection_is_completed_and_advances_checkpoint(fake_pipeline, monkeypatch):
    pipeline, state = fake_pipeline
    from nodes.telegram_dispatch import DispatchResult

    def reject(candidates, prompt):
        candidates[0].is_accepted = False
        return candidates

    monkeypatch.setattr(pipeline, "validate_batch", reject)
    async def no_dispatch(candidates):
        return DispatchResult()
    monkeypatch.setattr(pipeline, "dispatch_batch", no_dispatch)
    candidate = Candidate("owner/repo", "url", "description", 3, "Python", ["llm"])
    candidate.source_checkpoints = {"llm": "2026-01-01T00:00:00Z"}

    asyncio.run(pipeline.run_pipeline([candidate]))

    assert state.seen == ["owner/repo"]
    assert state.checkpoints == [{"llm": "2026-01-01T00:00:00Z"}]


def test_pipeline_passes_preference_memory(fake_pipeline, monkeypatch):
    pipeline, _state = fake_pipeline
    from nodes.telegram_dispatch import DispatchResult
    calls = []
    monkeypatch.setattr(
        pipeline,
        "generate_meta_prompt",
        lambda memory: (calls.append(memory), "meta")[1],
    )
    async def no_dispatch(_candidates):
        return DispatchResult()
    monkeypatch.setattr(pipeline, "dispatch_batch", no_dispatch)
    candidate = Candidate("owner/repo", "url", "desc", 10, "Python", [])

    asyncio.run(pipeline.run_pipeline([candidate]))

    assert len(calls) == 1
    assert calls[0].__class__.__name__ == "PreferenceMemory"


def test_validator_batch_propagates_unclassified_error(monkeypatch):
    import nodes.validator as validator

    def fail(*_args, **_kwargs):
        raise RuntimeError("LLM unavailable")
    monkeypatch.setattr(validator, "validate_candidate", fail)

    with pytest.raises(RuntimeError, match="LLM unavailable"):
        validator.validate_batch([Candidate("owner/repo", "url", "description", 0, "Python", [])], "meta")
