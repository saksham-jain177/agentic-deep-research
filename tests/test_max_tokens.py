"""Tests for LLM output-token cap (LLM_MAX_OUTPUT_TOKENS, default 4000)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import draft_agent
import evidence_extractor


class RecordingChatOpenAI:
    """Stands in for ChatOpenAI and records constructor kwargs."""

    last_kwargs = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs


@pytest.fixture
def api_keys(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "tvly-test-key-1234567890")


class TestDraftAgentMaxTokens:
    def test_get_llm_passes_default_max_tokens(self, monkeypatch, api_keys):
        monkeypatch.delenv("LLM_MAX_OUTPUT_TOKENS", raising=False)
        monkeypatch.setattr(draft_agent, "ChatOpenAI", RecordingChatOpenAI)
        monkeypatch.setattr(draft_agent, "llm", None)
        draft_agent._get_llm()
        assert RecordingChatOpenAI.last_kwargs["max_tokens"] == 4000

    def test_get_llm_respects_env_override(self, monkeypatch, api_keys):
        monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "1500")
        monkeypatch.setattr(draft_agent, "ChatOpenAI", RecordingChatOpenAI)
        monkeypatch.setattr(draft_agent, "llm", None)
        draft_agent._get_llm()
        assert RecordingChatOpenAI.last_kwargs["max_tokens"] == 1500

    def test_invalid_env_value_falls_back_to_default(self, monkeypatch, api_keys):
        monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "not-a-number")
        monkeypatch.setattr(draft_agent, "ChatOpenAI", RecordingChatOpenAI)
        monkeypatch.setattr(draft_agent, "llm", None)
        draft_agent._get_llm()
        assert RecordingChatOpenAI.last_kwargs["max_tokens"] == 4000

    def test_fallback_model_client_also_capped(self, monkeypatch):
        monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "2048")
        monkeypatch.setattr(draft_agent, "ChatOpenAI", RecordingChatOpenAI)
        draft_agent._llm_for_model("fb-a")
        assert RecordingChatOpenAI.last_kwargs["max_tokens"] == 2048


class TestEvidenceExtractorMaxTokens:
    def test_get_llm_passes_default_max_tokens(self, monkeypatch, api_keys):
        monkeypatch.delenv("LLM_MAX_OUTPUT_TOKENS", raising=False)
        monkeypatch.setattr(evidence_extractor, "ChatOpenAI", RecordingChatOpenAI)
        monkeypatch.setattr(evidence_extractor, "llm", None)
        evidence_extractor._get_llm()
        assert RecordingChatOpenAI.last_kwargs["max_tokens"] == 4000

    def test_get_llm_respects_env_override(self, monkeypatch, api_keys):
        monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "512")
        monkeypatch.setattr(evidence_extractor, "ChatOpenAI", RecordingChatOpenAI)
        monkeypatch.setattr(evidence_extractor, "llm", None)
        evidence_extractor._get_llm()
        assert RecordingChatOpenAI.last_kwargs["max_tokens"] == 512
