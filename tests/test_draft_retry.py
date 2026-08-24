"""Tests for per-section retry in draft_agent.draft_answer."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import draft_agent


class FakeLLM:
    """LLM stub that fails the first N invoke calls overall."""

    def __init__(self, failures_before_success=0, always_fail=False):
        self.calls = 0
        self.failures_before_success = failures_before_success
        self.always_fail = always_fail

    def invoke(self, messages):
        self.calls += 1
        if self.always_fail or self.calls <= self.failures_before_success:
            raise RuntimeError("transient LLM error")

        class Resp:
            content = "Section body."

        return Resp()


@pytest.fixture
def patched_llm(monkeypatch):
    def _install(llm):
        monkeypatch.setattr(draft_agent, "_get_llm", lambda: llm)
        monkeypatch.setattr(draft_agent.time, "sleep", lambda s: None)
        return llm

    return _install


def _draft(llm, deep_research=True):
    raw = draft_agent.draft_answer(
        data=[{"content": "some finding", "url": "https://example.com"}],
        deep_research=deep_research,
        retries=3,
        delay=0,
    )
    return raw, llm


def test_transient_failure_retries_only_failed_section(patched_llm):
    # Deep research has 6 sections; fail exactly once so only section 2 retries.
    llm = patched_llm(FakeLLM(failures_before_success=1))
    raw, llm = _draft(llm)
    result = json.loads(raw)

    assert len(result["sections"]) == 6
    assert [s["title"] for s in result["sections"]] == [
        "Abstract", "Introduction", "Literature Review",
        "Key Findings", "Analysis", "Conclusion",
    ]
    # 6 sections + 1 retry = 7 total invokes (not 2 full report passes = 12).
    assert llm.calls == 7


def test_section_exhausts_retries_returns_error(patched_llm):
    llm = patched_llm(FakeLLM(always_fail=True))
    raw, llm = _draft(llm)

    assert "Error drafting response" in raw
    # 3 attempts for the first section, then abort.
    assert llm.calls == 3


def test_no_failures_unchanged_behavior(patched_llm):
    llm = patched_llm(FakeLLM())
    raw, llm = _draft(llm)
    result = json.loads(raw)

    assert llm.calls == 6
    assert result["metadata"]["model"]
