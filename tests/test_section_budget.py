"""Per-section word budgets for the six-section deep report.

The prompt for every section used to receive ``target_word_count //
len(sections)``, so a 1000-word report asked the abstract for exactly as much
prose as the literature review and the key findings - which is why deep-mode
reports came out with a 300-word abstract and a thin evidence section.
"""

import json
import re

import pytest

import draft_agent
from draft_agent import _section_word_budget

DEEP_SECTIONS = [
    "Abstract", "Introduction", "Literature Review",
    "Key Findings", "Analysis", "Conclusion",
]

SOURCES = [
    {"title": f"Source {i}", "url": f"https://example.com/{i}", "content": "x"}
    for i in range(1, 9)
]


class TestBudgetMath:
    def test_evidence_sections_outrank_boilerplate(self):
        b = _section_word_budget(DEEP_SECTIONS, 1000)
        assert b["Key Findings"] > 2 * b["Abstract"]
        assert b["Literature Review"] > b["Abstract"]
        assert b["Analysis"] > b["Conclusion"]

    def test_budget_is_not_a_flat_split(self):
        assert len(set(_section_word_budget(DEEP_SECTIONS, 1000).values())) > 1

    def test_budget_targets_the_requested_total(self):
        assert sum(_section_word_budget(DEEP_SECTIONS, 1000).values()) == pytest.approx(1000, rel=0.05)
        assert sum(_section_word_budget(["Key Findings", "Analysis"], 400).values()) == pytest.approx(400, rel=0.05)

    def test_shallow_mode_favours_findings_over_analysis(self):
        b = _section_word_budget(["Key Findings", "Analysis"], 400)
        assert b["Key Findings"] > b["Analysis"]

    def test_unknown_section_gets_a_neutral_share(self):
        b = _section_word_budget(["Abstract", "Appendix"], 400)
        assert b["Appendix"] > b["Abstract"]

    def test_floor_keeps_every_section_worth_generating(self):
        assert all(v >= 60 for v in _section_word_budget(DEEP_SECTIONS, 120).values())


class RecordingLLM:
    """Returns a canned body per call and keeps the prompts it was sent."""

    def __init__(self, body):
        self.body = body
        self.prompts = []

    def invoke(self, messages):
        self.prompts.append(messages[-1]["content"])

        class Resp:
            pass

        Resp.content = self.body.format(n=len(self.prompts))
        return Resp()


@pytest.fixture
def recorded(monkeypatch):
    import contradiction_detector
    import verification_node

    def _install(body):
        llm = RecordingLLM(body)
        monkeypatch.setattr(draft_agent, "_get_llm", lambda: llm)
        monkeypatch.setattr(draft_agent.time, "sleep", lambda s: None)
        monkeypatch.setattr(verification_node, "verify_report", lambda *a, **k: [])
        monkeypatch.setattr(verification_node, "verify_section", lambda *a, **k: None)
        monkeypatch.setattr(contradiction_detector, "detect_contradictions", lambda *a, **k: [])
        return llm
    return _install


def _asked_word_counts(prompts):
    counts = [re.search(r"approximately (\d+) words", p) for p in prompts]
    assert all(counts), "every section prompt must state a word budget"
    return [int(m.group(1)) for m in counts]


def test_deep_prompts_ask_for_weighted_lengths(recorded):
    llm = recorded("Finding {n} backed by the evidence [1].")
    draft_agent.draft_answer(
        data=SOURCES, deep_research=True, target_word_count=1000)
    asked = _asked_word_counts(llm.prompts)
    assert len(asked) == len(DEEP_SECTIONS)
    abstract, _, lit_review, key_findings, _, _ = asked
    assert key_findings > 2 * abstract
    assert lit_review > abstract
    assert sum(asked) == pytest.approx(1000, rel=0.10)


def test_streaming_and_non_streaming_share_one_budget_rule(recorded):
    llm = recorded("Finding {n} backed by the evidence [1].")
    draft_agent.draft_answer(
        data=SOURCES, deep_research=True, target_word_count=1000)
    non_streamed = _asked_word_counts(llm.prompts)

    events = list(draft_agent.stream_draft_answer(
        data=SOURCES, deep_research=True, target_word_count=1000))
    streamed = _asked_word_counts(llm.prompts[len(non_streamed):])

    assert any(e.get("event") == "section" for e in events)
    assert streamed == non_streamed


def test_shallow_prompts_ask_for_two_weighted_sections(recorded):
    llm = recorded("Finding {n} backed by the evidence [1].")
    draft_agent.draft_answer(
        data=SOURCES, deep_research=False, target_word_count=400)
    assert _asked_word_counts(llm.prompts) == [220, 180]

