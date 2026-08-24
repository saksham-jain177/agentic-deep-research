"""Tests for inline [n] citation grounding (draft_agent._validate_citations)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import draft_agent


# --- _validate_citations --------------------------------------------------

def test_in_range_citations_kept_and_collected():
    text = "Warming accelerated [1]. Oceans absorbed heat too [2]."
    cleaned, cited = draft_agent._validate_citations(text, num_sources=2)
    assert cleaned == text
    assert cited == {1, 2}


def test_out_of_range_citation_stripped():
    cleaned, cited = draft_agent._validate_citations(
        "Claim from nowhere [9].", num_sources=3
    )
    assert "[9]" not in cleaned
    assert "nowhere" in cleaned
    assert cited == set()


def test_mixed_valid_and_invalid_citations():
    cleaned, cited = draft_agent._validate_citations(
        "Real claim [1] and phantom claim [42] and another [2].", num_sources=2
    )
    assert "[1]" in cleaned and "[2]" in cleaned
    assert "[42]" not in cleaned
    assert cited == {1, 2}


def test_whitespace_tidied_after_strip():
    cleaned, _ = draft_agent._validate_citations("End of sentence [99].", num_sources=1)
    # No double space or orphaned space before the period remains.
    assert "  " not in cleaned
    assert not cleaned.endswith(" .")
    assert "[99]" not in cleaned


def test_duplicate_citations_deduped_in_set():
    _, cited = draft_agent._validate_citations("A [1] B [1] C [1]", num_sources=2)
    assert cited == {1}


def test_zero_sources_returns_unchanged():
    text = "Nothing to ground [1]"
    cleaned, cited = draft_agent._validate_citations(text, num_sources=0)
    assert cleaned == text
    assert cited == set()


# --- _uncited_paragraphs ---------------------------------------------------

def test_uncited_paragraph_flagged():
    text = "Cited paragraph [1].\n\nUncited paragraph without refs."
    flagged = draft_agent._uncited_paragraphs(text)
    assert flagged == [1]


def test_all_cited_paragraphs_not_flagged():
    text = "First has [1].\n\nSecond has [2] as well."
    assert draft_agent._uncited_paragraphs(text) == []


def test_out_of_range_only_paragraph_counts_as_uncited():
    # After stripping, the paragraph carries no valid citation -> still flagged.
    text, _ = draft_agent._validate_citations("Phantom only [7].", num_sources=2)
    assert draft_agent._uncited_paragraphs(text) == [0]


# --- draft_answer integration ---------------------------------------------

class CitingFakeLLM:
    """LLM stub that emits section bodies with inline citations."""

    def __init__(self, body):
        self.body = body
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1

        class Resp:
            pass

        Resp.content = self.body
        return Resp()


@pytest.fixture
def citing_llm(monkeypatch):
    def _install(body):
        llm = CitingFakeLLM(body)
        monkeypatch.setattr(draft_agent, "_get_llm", lambda: llm)
        monkeypatch.setattr(draft_agent.time, "sleep", lambda s: None)
        return llm

    return _install


SOURCES = [
    {"title": "Source One", "url": "https://example.com/one", "content": "x"},
    {"title": "Source Two", "url": "https://example.org/two", "content": "y"},
]


def test_references_only_include_cited_ids(citing_llm):
    citing_llm("Body cites source one only [1].")
    raw = draft_agent.draft_answer(data=SOURCES, deep_research=False)
    result = json.loads(raw)

    assert len(result["references"]) == 1
    assert "Source One" in result["references"][0]


def test_out_of_range_refs_stripped_from_final_sections(citing_llm):
    citing_llm("Valid [1] then phantom [5].")
    raw = draft_agent.draft_answer(data=SOURCES, deep_research=False)
    result = json.loads(raw)

    for section in result["sections"]:
        assert "[5]" not in section["content"]
        assert "[1]" in section["content"]
    assert len(result["references"]) == 1


def test_no_citations_means_empty_references(citing_llm):
    citing_llm("Plain body with no citations at all.")
    raw = draft_agent.draft_answer(data=SOURCES, deep_research=False)
    result = json.loads(raw)

    assert result["references"] == []


def test_system_prompt_requests_inline_citations(citing_llm):
    captured = {}

    def invoke(messages):
        captured["system"] = messages[0]["content"]

        class Resp:
            content = "Body [1]."

        return Resp()

    llm = CitingFakeLLM("")
    llm.invoke = invoke
    monkey_llm = llm
    import draft_agent as da

    original = da._get_llm
    da._get_llm = lambda: monkey_llm
    try:
        draft_agent.draft_answer(data=SOURCES, deep_research=False)
    finally:
        da._get_llm = original

    assert "[1]" in captured["system"]
    assert "evidence table" in captured["system"]
