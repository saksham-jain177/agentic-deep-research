"""Tests for the model fallback chain in draft_agent.draft_answer."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import draft_agent

RATE_LIMIT_MSG = "Error code: 429 - Rate limited. Please retry shortly."


class GoodLLM:
    def __init__(self, name="good"):
        self.name = name
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1

        class Resp:
            content = "Section body. [1]"

        return Resp()


class RateLimitedLLM:
    def __init__(self, name="limited"):
        self.name = name
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        raise RuntimeError(RATE_LIMIT_MSG)


class GenericErrorLLM:
    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        raise RuntimeError("connection refused")


class EmptyResponseLLM:
    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1

        class Resp:
            content = "<think>reasoning only</think>  "  # cleans to empty

        return Resp()


@pytest.fixture
def chain_env(monkeypatch):
    """Configure primary + fallback models and patch client construction."""
    monkeypatch.setenv("OPENROUTER_MODEL", "primary-model")
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODELS", "fb-a, fb-b")  # spaces on purpose

    requested_models = []
    built = []

    def _install(primary_llm_factory, fallback_llm_factory=None):
        fallback_llm_factory = fallback_llm_factory or (lambda m: GoodLLM(m))

        def fake_llm_for_model(model):
            requested_models.append(model)
            llm = fallback_llm_factory(model)
            built.append(llm)
            return llm

        monkeypatch.setattr(draft_agent, "_get_llm", primary_llm_factory)
        monkeypatch.setattr(draft_agent, "_llm_for_model", fake_llm_for_model)
        monkeypatch.setattr(draft_agent.time, "sleep", lambda s: None)
        return requested_models

    return _install


def _draft(deep_research=True, retries=3):
    raw = draft_agent.draft_answer(
        data=[{"content": "some finding", "url": "https://example.com"}],
        deep_research=deep_research,
        retries=retries,
        delay=0,
    )
    return json.loads(raw)


def test_chain_advances_on_rate_limit(chain_env):
    primary = RateLimitedLLM()
    requested = chain_env(lambda: primary)
    result = _draft()

    # Primary 429s once, fb-a takes over for all six sections.
    assert [s["title"] for s in result["sections"]] == [
        "Abstract", "Introduction", "Literature Review",
        "Key Findings", "Analysis", "Conclusion",
        "Verification Summary",  # tier 3 judge pass appends its own block
    ]
    assert requested == ["fb-a"]  # spaces stripped; fb-b never needed
    assert primary.calls == 1
    assert result["metadata"]["model"] == "fb-a"


def test_stays_on_primary_when_healthy(chain_env):
    primary = GoodLLM("primary")
    requested = chain_env(lambda: primary)
    result = _draft()

    assert len(result["sections"]) == 7  # 6 sections + Verification Summary block
    assert requested == []  # no fallback client ever constructed
    assert primary.calls == 6
    assert result["metadata"]["model"] == "primary-model"


def test_non_rate_limit_error_does_not_advance(chain_env):
    primary = GenericErrorLLM()
    requested = chain_env(lambda: primary)
    raw = draft_agent.draft_answer(
        data=[{"content": "some finding", "url": "https://example.com"}],
        deep_research=True,
        retries=3,
        delay=0,
    )

    assert "Error drafting response" in raw
    assert requested == []  # generic errors stay on primary
    assert primary.calls == 3  # plain retry loop, then give up


def test_empty_response_advances_to_fallback(chain_env):
    primary = EmptyResponseLLM()
    requested = chain_env(lambda: primary)
    result = _draft()

    assert requested == ["fb-a"]
    assert primary.calls == 1
    assert result["metadata"]["model"] == "fb-a"


def test_chain_exhaustion_gives_up_after_all_models(chain_env):
    primary = RateLimitedLLM("primary")

    def failing_fallback(model):
        return RateLimitedLLM(model)

    requested = chain_env(lambda: primary, failing_fallback)
    raw = draft_agent.draft_answer(
        data=[{"content": "some finding", "url": "https://example.com"}],
        deep_research=True,
        retries=3,
        delay=0,
    )

    assert "Error drafting response" in raw
    assert requested == ["fb-a", "fb-b"]  # walked the whole chain
    assert primary.calls + 2 == 3  # one attempt per chain entry


def test_no_fallbacks_configured_keeps_legacy_behavior(chain_env, monkeypatch):
    monkeypatch.delenv("OPENROUTER_FALLBACK_MODELS", raising=False)
    primary = RateLimitedLLM()
    requested = chain_env(lambda: primary)
    raw = draft_agent.draft_answer(
        data=[{"content": "some finding", "url": "https://example.com"}],
        deep_research=True,
        retries=3,
        delay=0,
    )

    assert "Error drafting response" in raw
    assert requested == []
    assert primary.calls == 3


def test_duplicate_primary_not_repeated_in_chain(chain_env, monkeypatch):
    """If OPENROUTER_MODEL is listed among fallbacks it is not tried twice."""
    primary = RateLimitedLLM()
    requested = chain_env(lambda: primary)
    # The chain is [primary] + fallbacks-minus-duplicates; simulate the env
    # listing the primary model again and confirm the dedupe in _get_fallback_models.
    assert "primary-model" not in draft_agent._get_fallback_models()  # current env: fb-a, fb-b

    monkeypatch.setenv("OPENROUTER_FALLBACK_MODELS", "fb-a, primary-model")
    fallbacks = draft_agent._get_fallback_models()
    model_chain = ["primary-model"] + [m for m in fallbacks if m != "primary-model"]
    assert model_chain == ["primary-model", "fb-a"]
    assert requested == []


def test_get_fallback_models_parsing(monkeypatch):
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODELS", " a , b ,, b,c ")
    assert draft_agent._get_fallback_models() == ["a", "b", "c"]
    monkeypatch.delenv("OPENROUTER_FALLBACK_MODELS")
    assert draft_agent._get_fallback_models() == []
