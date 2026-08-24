"""Regression tests for Tier 4 bugfixes.

BUG 1: stream_cli.py non-mock path called a nonexistent
research_agent.research(); it must use research_web().
BUG 2: stream_draft_answer dropped the language param when calling
verification_node.verify_section() and contradiction_detector.
detect_contradictions(), so streamed reports lost multilingual
verification threading that draft_answer has.

All LLM/network calls are mocked; no API keys required.
"""

import sys
from unittest.mock import patch, MagicMock

import pytest

import draft_agent
import research_agent
import stream_cli
from draft_agent import (
    stream_draft_answer,
    STREAM_EVENT_DONE,
)


@pytest.fixture
def sample_data():
    return [
        {"title": "Climate report", "url": "https://example.com/climate",
         "content": "Global wheat yields fall by 6% per degree of warming."},
        {"title": "Agri study", "url": "https://example.org/agri",
         "content": "Farmers adapt via drought-resistant cultivars."},
    ]


def _resp(content):
    m = MagicMock()
    m.content = content
    return m


GOOD_TEXT = ("Yields drop 6% per degree [1].\n\n"
             "Adaptation uses resistant cultivars [2].")


def _stream_env(invoke_side_effects):
    """Offline patch stack mirroring tests/test_streaming_report.py."""
    fake_llm = MagicMock()
    fake_llm.invoke.side_effect = invoke_side_effects
    return [
        patch.object(draft_agent, "_get_llm", lambda: fake_llm),
        patch("draft_agent.evidence_extractor.extract_evidence", lambda d: d),
        patch("draft_agent.evidence_extractor.render_evidence_table",
              lambda e, d: "TABLE"),
        patch.object(draft_agent, "enforce_token_budget",
                     lambda ev, d, deep, n: (ev, {})),
        patch("time.sleep"),
    ]


class TestCliRealPathUsesResearchWeb:
    """BUG 1 regression: the non-mock CLI path must resolve the real
    research_agent entry point (research_web), not a nonexistent one."""

    def test_main_calls_research_web_with_query_and_deep_flag(
            self, monkeypatch):
        calls = {}

        def fake_research_web(query, deep_research=False, language="en"):
            calls["query"] = query
            calls["deep_research"] = deep_research
            return [{"title": "T", "url": "https://example.com/x",
                     "content": "Some content [1]."}]

        def fake_stream(*args, **kwargs):
            yield {
                "event": STREAM_EVENT_DONE,
                "sections": [],
                "references": [],
                "metadata": {},
            }

        monkeypatch.setattr(research_agent, "research_web",
                            fake_research_web)
        monkeypatch.setattr(draft_agent, "stream_draft_answer", fake_stream)
        monkeypatch.setattr(
            sys, "argv",
            ["stream_cli.py", "climate impact on spanish agriculture",
             "--deep"])

        rc = stream_cli.main()

        assert rc == 0
        assert calls["query"] == "climate impact on spanish agriculture"
        assert calls["deep_research"] is True

    def test_main_default_mode_passes_deep_false(self, monkeypatch):
        calls = {}

        def fake_research_web(query, deep_research=False, language="en"):
            calls["deep_research"] = deep_research
            return [{"title": "T", "url": "https://example.com/y",
                     "content": "Content."}]

        monkeypatch.setattr(research_agent, "research_web",
                            fake_research_web)
        monkeypatch.setattr(
            draft_agent, "stream_draft_answer",
            lambda *a, **k: iter([{"event": STREAM_EVENT_DONE,
                                   "sections": [], "references": [],
                                   "metadata": {}}]))
        monkeypatch.setattr(sys, "argv",
                            ["stream_cli.py", "plain query"])

        assert stream_cli.main() == 0
        assert calls["deep_research"] is False


class TestStreamLanguageThreading:
    """BUG 2 regression: stream_draft_answer must thread its language
    into per-section verification and the contradiction pass, matching
    what draft_answer does (PR #14)."""

    def _run_spanish_stream(self, sample_data):
        seen_verify = []
        seen_contra = []

        def fake_verify(title, text, data, **kwargs):
            seen_verify.append(kwargs.get("language"))
            return {"section": title, "verdict": "supported",
                    "unsupported_claims": [], "citations_coverage": 1.0,
                    "notes": ""}

        def fake_detect(data, **kwargs):
            seen_contra.append(kwargs.get("language"))
            return []

        patches = _stream_env([_resp(GOOD_TEXT)] * 2)
        patches.append(patch("verification_node.verify_section",
                             side_effect=fake_verify))
        patches.append(patch("contradiction_detector.detect_contradictions",
                             side_effect=fake_detect))
        for p in patches:
            p.start()
        try:
            events = list(stream_draft_answer(sample_data, deep_research=False,
                                              language="spanish"))
        finally:
            for p in patches:
                p.stop()
        return events, seen_verify, seen_contra

    def test_verification_receives_language(self, sample_data):
        events, seen_verify, _ = self._run_spanish_stream(sample_data)

        sections = [e for e in events if e["event"] == "section"]
        assert len(sections) == 2          # sanity: both sections verified
        assert seen_verify == ["spanish", "spanish"]

    def test_contradiction_pass_receives_language(self, sample_data):
        _, _, seen_contra = self._run_spanish_stream(sample_data)

        assert seen_contra == ["spanish"]

    def test_stream_still_completes_in_spanish(self, sample_data):
        events, _, _ = self._run_spanish_stream(sample_data)

        assert events[-1]["event"] == STREAM_EVENT_DONE
        assert events[-1]["metadata"]["language"] == "spanish"
