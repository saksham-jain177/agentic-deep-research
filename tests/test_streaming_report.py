"""Offline tests for streaming report generation (Tier 4).

All LLM calls are mocked; no API keys required.
"""

from unittest.mock import patch, MagicMock

import pytest

import draft_agent
from draft_agent import (
    stream_draft_answer,
    collect_streamed_report,
    STREAM_EVENT_SECTION,
    STREAM_EVENT_SECTION_ERROR,
    STREAM_EVENT_CONTRADICTIONS,
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


SECTION_TITLES_SHALLOW = ["Key Findings", "Analysis"]
SECTION_TITLES_DEEP = [
    "Abstract", "Introduction", "Literature Review",
    "Key Findings", "Analysis", "Conclusion",
]


def _resp(content):
    m = MagicMock()
    m.content = content
    return m


GOOD_TEXT = ("Yields drop 6% per degree [1].\n\n"
             "Adaptation uses resistant cultivars [2].")


def _stream_env(invoke_side_effects, verify=None, contradictions=None,
                retries=3):
    """Build a context manager stack mocking everything offline.

    Usage:
        with _stream_env([...]) as ctx:
            events = list(stream_draft_answer(...))
    """
    fake_llm = MagicMock()
    fake_llm.invoke.side_effect = invoke_side_effects
    cm = [
        patch.object(draft_agent, "_get_llm", lambda: fake_llm),
        patch("draft_agent.evidence_extractor.extract_evidence", lambda d: d),
        patch("draft_agent.evidence_extractor.render_evidence_table",
              lambda e, d: "TABLE"),
        patch.object(draft_agent, "enforce_token_budget",
                     lambda ev, d, deep, n: (ev, {})),
        patch("time.sleep"),
    ]
    if verify is not None:
        if not callable(verify):
            _fixed = verify
            verify = lambda title, text, data: _fixed
        cm.append(patch("verification_node.verify_section", side_effect=verify))
    if contradictions is not None:
        cm.append(patch("contradiction_detector.detect_contradictions",
                        return_value=contradictions))
    return cm


class TestSectionOrdering:
    def test_events_and_order(self, sample_data):
        patches = _stream_env([_resp(GOOD_TEXT)] * 2)
        for p in patches:
            p.start()
        try:
            events = list(stream_draft_answer(sample_data, deep_research=False))
        finally:
            for p in patches:
                p.stop()

        kinds = [e["event"] for e in events]
        assert kinds[0] == STREAM_EVENT_SECTION
        assert kinds[-1] == STREAM_EVENT_DONE
        section_titles = [e["title"] for e in events
                          if e["event"] == STREAM_EVENT_SECTION]
        assert section_titles == SECTION_TITLES_SHALLOW

        done = events[-1]
        assert [s["title"] for s in done["sections"]] == SECTION_TITLES_SHALLOW
        assert isinstance(done["references"], list)
        assert done["metadata"]["language"] == "english"

    def test_deep_mode_has_six_sections(self, sample_data):
        patches = _stream_env([_resp(GOOD_TEXT)] * 6)
        for p in patches:
            p.start()
        try:
            events = list(stream_draft_answer(sample_data, deep_research=True))
        finally:
            for p in patches:
                p.stop()
        titles = [e["title"] for e in events
                  if e["event"] == STREAM_EVENT_SECTION]
        assert titles == SECTION_TITLES_DEEP


class TestIncrementalVerification:
    def test_verification_attached_to_each_section_event(self, sample_data):
        verdict = {"section": "S", "verdict": "supported",
                   "unsupported_claims": [], "citations_coverage": 1.0,
                   "notes": ""}
        patches = _stream_env([_resp(GOOD_TEXT)] * 2, verify=verdict)
        for p in patches:
            p.start()
        verify_mock = [p for p in patches
                       if getattr(p, "attribute", "") == "verify_section"]
        try:
            events = list(stream_draft_answer(sample_data, deep_research=False))
        finally:
            for p in patches:
                p.stop()

        sections = [e for e in events if e["event"] == STREAM_EVENT_SECTION]
        assert sections and all(e["verification"] == verdict for e in sections)

    def test_verification_runs_before_stream_finishes(self, sample_data):
        """Per-section verification: each verdict is available as soon as its
        section is yielded, not after one blocking tail pass."""
        order = []

        def verify(title, text, data):
            order.append(f"verify:{title}")
            return {"section": title, "verdict": "supported",
                    "unsupported_claims": [], "citations_coverage": 1.0,
                    "notes": ""}

        def consume():
            for event in stream_draft_answer(sample_data, deep_research=False):
                if event["event"] == STREAM_EVENT_SECTION:
                    order.append(f"yield:{event['title']}")
                elif event["event"] == STREAM_EVENT_DONE:
                    order.append("done")

        patches = _stream_env([_resp(GOOD_TEXT)] * 2, verify=verify)
        for p in patches:
            p.start()
        try:
            consume()
        finally:
            for p in patches:
                p.stop()

        # Interleaved: verify(Key Findings) happens before yield(Analysis).
        assert order.index("verify:Key Findings") < order.index("yield:Analysis")
        assert order[-1] == "done"

    def test_verification_crash_does_not_break_stream(self, sample_data):
        # verify_section itself falls back to heuristics on internal errors;
        # patch deeper so the LLM judge call explodes, and confirm the stream
        # still completes with a usable verdict attached per section.
        patches = _stream_env([_resp(GOOD_TEXT)] * 2)
        patches.append(patch(
            "verification_node.build_verification_prompt",
            side_effect=RuntimeError("boom")))
        for p in patches:
            p.start()
        try:
            events = list(stream_draft_answer(sample_data, deep_research=False))
        finally:
            for p in patches:
                p.stop()
        assert events[-1]["event"] == STREAM_EVENT_DONE
        sections = [e for e in events if e["event"] == STREAM_EVENT_SECTION]
        assert sections
        for e in sections:
            v = e["verification"]
            assert v is not None
            assert "Heuristic verdict" in v["notes"]


class TestErrorResilience:
    def test_section_error_event_and_continuation(self, sample_data):
        effects = [_resp("Good content [1]."), _resp("   "),
                   _resp("Recovered [2].")] + [_resp(GOOD_TEXT)] * 3
        patches = _stream_env(effects, contradictions=[], retries=1)
        for p in patches:
            p.start()
        try:
            events = list(stream_draft_answer(sample_data, deep_research=True,
                                              retries=1))
        finally:
            for p in patches:
                p.stop()

        errors = [e for e in events if e["event"] == STREAM_EVENT_SECTION_ERROR]
        assert len(errors) == 1
        assert errors[0]["index"] == 1
        assert errors[0]["title"] == "Introduction"
        assert "empty response" in errors[0]["error"]

        ok_sections = [e for e in events if e["event"] == STREAM_EVENT_SECTION]
        assert 1 not in [e["index"] for e in ok_sections]
        assert events[-1]["event"] == STREAM_EVENT_DONE
        assert [s["title"] for s in events[-1]["sections"]] == [
            t for t in SECTION_TITLES_DEEP if t != "Introduction"]

    def test_missing_api_key_yields_done_with_error(self, sample_data):
        with patch.object(draft_agent, "_get_llm", lambda: None):
            events = list(stream_draft_answer(sample_data))
        assert len(events) == 1
        assert events[0]["event"] == STREAM_EVENT_DONE
        assert "Missing API key" in events[0]["error"]

    def test_no_data(self):
        with patch.object(draft_agent, "_get_llm", lambda: object()):
            events = list(stream_draft_answer([]))
        assert events[0]["event"] == STREAM_EVENT_DONE
        assert "No research data" in events[0]["error"]


class TestContradictionEvent:
    def test_contradictions_emitted_before_done(self, sample_data):
        items = [{"claim_a": "a", "source_id_a": 1, "claim_b": "b",
                  "source_id_b": 2, "topic": "t", "severity": "low"}]
        patches = _stream_env([_resp(GOOD_TEXT)] * 2, contradictions=items)
        for p in patches:
            p.start()
        try:
            events = list(stream_draft_answer(sample_data, deep_research=False))
        finally:
            for p in patches:
                p.stop()
        kinds = [e["event"] for e in events]
        assert kinds.index(STREAM_EVENT_CONTRADICTIONS) == len(kinds) - 2
        assert events[-2]["items"] == items


class TestCollectStreamedReport:
    def test_collect_shape(self, sample_data):
        verdict = {"section": "S", "verdict": "supported",
                   "unsupported_claims": [], "citations_coverage": 1.0,
                   "notes": ""}
        patches = _stream_env([_resp(GOOD_TEXT)] * 2, verify=verdict,
                              contradictions=[])
        for p in patches:
            p.start()
        try:
            result = collect_streamed_report(
                stream_draft_answer(sample_data, deep_research=False))
        finally:
            for p in patches:
                p.stop()
        assert set(result) >= {"sections", "references", "metadata",
                               "verification", "contradictions"}
        assert result["verification"] == [verdict] * 2
        assert [s["title"] for s in result["sections"]] == SECTION_TITLES_SHALLOW


# The first placeholder test is removed by design; keep a valid marker test.
def test_placeholder_removed():
    assert SECTION_TITLES_SHALLOW[0] == "Key Findings"
