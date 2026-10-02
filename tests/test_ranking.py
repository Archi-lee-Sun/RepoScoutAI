from state import Candidate


def candidate(name, score, stars=0, clusters=None):
    return Candidate(
        name, f"https://github.com/{name}", "developer workflow automation",
        stars, "Python", clusters or ["agents"],
        selector_accepted=True, selector_score=score, readme="evidence",
    )


def test_rank_finalists_prioritizes_score_and_caps(monkeypatch):
    import pipeline

    monkeypatch.setenv("MAX_TELEGRAM_RECOMMENDATIONS", "2")
    weak_popular = candidate("x/popular", 71, stars=100_000)
    strong_new = candidate("x/new", 88, stars=0)
    middle = candidate("x/middle", 80, stars=1)

    finalists = pipeline.rank_finalists([weak_popular, middle, strong_new])

    assert [c.full_name for c in finalists] == ["x/new", "x/middle"]
    assert strong_new.is_finalist and middle.is_finalist
    assert not weak_popular.is_finalist


def test_invalid_recommendation_limit_uses_default(monkeypatch):
    import pipeline

    monkeypatch.setenv("MAX_TELEGRAM_RECOMMENDATIONS", "many")
    assert pipeline._recommendation_limit() == 8


def test_non_finalists_do_not_reach_explainer_or_translator(monkeypatch):
    import nodes.explainer as explainer
    import nodes.translator as translator

    non_finalist = candidate("x/skip", 60)
    finalist = candidate("x/send", 90)
    non_finalist.is_finalist = False
    finalist.is_finalist = True
    calls = []
    monkeypatch.setattr(explainer, "explain_repository", lambda item: calls.append(("explain", item.full_name)))
    monkeypatch.setattr(translator, "translate_repository", lambda item: calls.append(("translate", item.full_name)))

    explainer.explain_batch([non_finalist, finalist])
    finalist.explanation_en = "Short explanation"
    non_finalist.explanation_en = "Should not be translated"
    translator.translate_batch([non_finalist, finalist])

    assert calls == [("explain", "x/send"), ("translate", "x/send")]


def test_selector_persists_calibrated_score(monkeypatch):
    import nodes.selector as selector

    item = candidate("x/repo", 0)

    class FakeStructuredLLM:
        def invoke(self, _messages):
            return selector.SelectorDecision(accept=True, score=83, reason="Useful API in src/client.py.")

    monkeypatch.setattr(selector, "structured_llm", FakeStructuredLLM())
    selector.select_candidate(item, "taste")

    assert item.selector_accepted is True
    assert item.selector_score == 83


def test_selector_score_schema_rejects_out_of_range():
    import pytest
    from pydantic import ValidationError
    from nodes.selector import SelectorDecision

    with pytest.raises(ValidationError):
        SelectorDecision(accept=True, score=101, reason="invalid")


def test_selector_and_translation_prompts_cover_relevance_and_terse_translation():
    from prompts import get_selector_prompt, get_translator_prompt

    selector_prompt = get_selector_prompt("taste")
    translator_prompt = get_translator_prompt()
    assert "practical value" in selector_prompt
    assert "Stars are only a weak tie-breaker" in selector_prompt
    assert "score (integer 0-100)" in selector_prompt
    assert "keep the Georgian translation close in length" in translator_prompt
    assert "preserve repository/library/product/model names" in translator_prompt
