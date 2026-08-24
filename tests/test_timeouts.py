"""Tests for explicit request timeouts and retry budgets (reliability)."""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import draft_agent
import evidence_extractor
import research_agent


class RecordingChatOpenAI:
    """Stands in for ChatOpenAI and records constructor kwargs."""

    last_kwargs = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs


@pytest.fixture
def api_keys(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "tvly-test-key-1234567890")


class TestDraftAgentTimeouts:
    def test_get_llm_passes_default_timeout_and_retries(self, monkeypatch, api_keys):
        monkeypatch.setattr(draft_agent, "ChatOpenAI", RecordingChatOpenAI)
        monkeypatch.setattr(draft_agent, "llm", None)
        draft_agent._get_llm()

        kwargs = RecordingChatOpenAI.last_kwargs
        assert kwargs["timeout"] == 60
        assert kwargs["max_retries"] == 2

    def test_timeout_config_env_overrides(self, monkeypatch, api_keys):
        monkeypatch.setattr(draft_agent, "ChatOpenAI", RecordingChatOpenAI)
        monkeypatch.setattr(draft_agent, "llm", None)
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "15")
        monkeypatch.setenv("LLM_MAX_RETRIES", "5")
        draft_agent._get_llm()

        kwargs = RecordingChatOpenAI.last_kwargs
        assert kwargs["timeout"] == 15
        assert kwargs["max_retries"] == 5

    def test_invalid_env_values_fall_back_to_defaults(self, monkeypatch, api_keys):
        monkeypatch.setattr(draft_agent, "ChatOpenAI", RecordingChatOpenAI)
        monkeypatch.setattr(draft_agent, "llm", None)
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "not-a-number")
        monkeypatch.setenv("LLM_MAX_RETRIES", "-3")
        draft_agent._get_llm()

        kwargs = RecordingChatOpenAI.last_kwargs
        assert kwargs["timeout"] == 60
        assert kwargs["max_retries"] == 2

    def test_llm_for_model_passes_model_and_timeout(self, monkeypatch, api_keys):
        monkeypatch.setattr(draft_agent, "ChatOpenAI", RecordingChatOpenAI)
        draft_agent._llm_for_model("fb-a")

        kwargs = RecordingChatOpenAI.last_kwargs
        assert kwargs["model"] == "fb-a"
        assert kwargs["timeout"] == 60
        assert kwargs["max_retries"] == 2


class TestEvidenceExtractorTimeouts:
    def test_get_llm_passes_default_timeout_and_retries(self, monkeypatch, api_keys):
        monkeypatch.setattr(evidence_extractor, "ChatOpenAI", RecordingChatOpenAI)
        monkeypatch.setattr(evidence_extractor, "llm", None)
        evidence_extractor._get_llm()

        kwargs = RecordingChatOpenAI.last_kwargs
        assert kwargs["timeout"] == 60
        assert kwargs["max_retries"] == 2

    def test_timeout_config_env_overrides(self, monkeypatch, api_keys):
        monkeypatch.setattr(evidence_extractor, "ChatOpenAI", RecordingChatOpenAI)
        monkeypatch.setattr(evidence_extractor, "llm", None)
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "30")
        monkeypatch.setenv("LLM_MAX_RETRIES", "1")
        evidence_extractor._get_llm()

        kwargs = RecordingChatOpenAI.last_kwargs
        assert kwargs["timeout"] == 30
        assert kwargs["max_retries"] == 1


class FakeTavilyClient:
    search_calls = []

    def __init__(self, api_key=None):
        pass

    def search(self, query, **kwargs):
        type(self).search_calls.append(kwargs)
        return {"results": []}


@pytest.fixture
def stub_tavily(monkeypatch):
    FakeTavilyClient.search_calls = []
    # vector_store pulls in chromadb/sentence-transformers; stub it so the
    # rerank block inside research_web stays hermetic and fast.
    stub = types.ModuleType("vector_store")
    stub.rerank_documents = lambda query, docs, top_k=10: docs[:top_k]
    monkeypatch.setitem(sys.modules, "vector_store", stub)
    monkeypatch.setattr(research_agent, "TavilyClient", FakeTavilyClient)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-1234567890")
    return FakeTavilyClient


class TestTavilyTimeouts:
    def test_search_uses_default_timeout(self, stub_tavily):
        research_agent.research_web("quantum computing")
        assert len(stub_tavily.search_calls) == 1
        assert stub_tavily.search_calls[0]["timeout"] == pytest.approx(60.0)

    def test_search_respects_env_timeout(self, stub_tavily, monkeypatch):
        monkeypatch.setenv("RESEARCH_TIMEOUT_SECONDS", "25")
        research_agent.research_web("quantum computing")
        assert stub_tavily.search_calls[0]["timeout"] == pytest.approx(25.0)

    def test_variant_queries_also_carry_timeout(self, stub_tavily, monkeypatch):
        # Deep mode with <10 results triggers the parallel variant queries.
        monkeypatch.setenv("RESEARCH_TIMEOUT_SECONDS", "10")

        def few_results(self, query, **kwargs):
            type(self).search_calls.append(kwargs)
            return {
                "results": [
                    {"title": "t", "content": "c", "url": f"https://e.com/{abs(hash(query)) % 997}"}
                ]
            }

        stub_tavily.search = few_results

        research_agent.research_web("quantum computing", deep_research=True)
        assert all(call["timeout"] == pytest.approx(10.0) for call in stub_tavily.search_calls)
        assert len(stub_tavily.search_calls) >= 3  # initial + variants
