"""Tests for evidence_extractor: extraction fallback, normalization, rendering.

All tests are mocked / deterministic — no real LLM calls.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evidence_extractor


SAMPLE_DATA = [
    {
        "title": "Climate Report",
        "url": "https://example.com/climate",
        "content": (
            "Global temperatures rose 1.2 degrees Celsius above pre-industrial levels "
            "in the last decade according to compiled records. Agricultural yields in "
            "temperate zones shifted poleward measurably during the same period of study. "
            "Short. Ok"
        ),
    },
    {
        "title": "Ocean Warming Study",
        "url": "https://example.org/ocean",
        "content": (
            "Ocean heat content reached a record high in 2024, continuing a multi-decade "
            "warming trend observed across all basins worldwide by research vessels."
        ),
    },
]


class FakeLLM:
    """LLM stub returning a canned response body."""

    def __init__(self, content="[]", fail=False):
        self.content = content
        self.fail = fail
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.fail:
            raise RuntimeError("LLM down")

        class Resp:
            pass

        Resp.content = self.content
        return Resp()


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr(evidence_extractor, "_get_llm", lambda: None)


@pytest.fixture
def fake_llm(monkeypatch):
    def _install(llm):
        monkeypatch.setattr(evidence_extractor, "_get_llm", lambda: llm)
        monkeypatch.setattr(evidence_extractor.time, "sleep", lambda s: None)
        return llm

    return _install


# --- _fallback_evidence -------------------------------------------------

def test_fallback_shape_and_confidence(no_llm):
    evidence = evidence_extractor._fallback_evidence(SAMPLE_DATA)

    assert evidence, "fallback must produce claims for non-empty data"
    for entry in evidence:
        assert set(entry.keys()) == {"claim", "source_id", "quote", "confidence"}
        assert entry["confidence"] == "low"
        assert isinstance(entry["source_id"], int)


def test_fallback_source_ids_are_one_based(no_llm):
    evidence = evidence_extractor._fallback_evidence(SAMPLE_DATA)
    ids = {e["source_id"] for e in evidence}
    assert ids == {1, 2}


def test_fallback_skips_short_sentences(no_llm):
    evidence = evidence_extractor._fallback_evidence(SAMPLE_DATA)
    # "Short." and "Ok" are <25 chars and must never appear as claims.
    assert all(len(e["claim"]) >= 25 for e in evidence)


def test_fallback_caps_three_sentences_per_source(no_llm):
    long_content = {"title": "T", "url": "", "content": ". ".join(
        f"Sentence number {i} carries some substance here" for i in range(10)
    ) + "."}
    evidence = evidence_extractor._fallback_evidence([long_content])
    assert len(evidence) <= 3


def test_fallback_empty_data(no_llm):
    assert evidence_extractor._fallback_evidence([]) == []


# --- _parse_evidence_json ----------------------------------------------

def test_parse_strips_think_tags_and_fences():
    raw = "<think>reasoning here</think>\n```json\n[{\"claim\": \"x\"}]\n```"
    items = evidence_extractor._parse_evidence_json(raw)
    assert items == [{"claim": "x"}]


def test_parse_raises_on_garbage():
    with pytest.raises(ValueError):
        evidence_extractor._parse_evidence_json("not json at all")


def test_parse_raises_on_non_array():
    with pytest.raises(ValueError):
        evidence_extractor._parse_evidence_json('{"claim": "object not array"}')


# --- _normalize_entries -------------------------------------------------

def test_normalize_drops_invalid_entries():
    items = [
        {"claim": "good", "source_id": 1, "quote": "q", "confidence": "high"},
        "not-a-dict",
        {"claim": "", "source_id": 1},              # empty claim
        {"claim": "bad id", "source_id": "abc"},     # unparsable id
        {"claim": "out of range", "source_id": 9},   # beyond num_sources
        {"claim": "zero id", "source_id": 0},        # below range
    ]
    evidence = evidence_extractor._normalize_entries(items, num_sources=2)
    assert [e["claim"] for e in evidence] == ["good"]


def test_normalize_defaults_confidence_to_medium():
    items = [{"claim": "c", "source_id": 1, "confidence": "bogus"}]
    evidence = evidence_extractor._normalize_entries(items, num_sources=1)
    assert evidence[0]["confidence"] == "medium"


def test_normalize_truncates_long_quotes():
    items = [{"claim": "c", "source_id": 1, "quote": "x" * 500}]
    evidence = evidence_extractor._normalize_entries(items, num_sources=1)
    assert len(evidence[0]["quote"]) == evidence_extractor.MAX_QUOTE_CHARS


def test_normalize_respects_max_claims():
    items = [{"claim": f"c{i}", "source_id": 1} for i in range(100)]
    evidence = evidence_extractor._normalize_entries(items, num_sources=1)
    assert len(evidence) == evidence_extractor.MAX_CLAIMS


# --- extract_evidence ----------------------------------------------------

def test_extract_without_llm_uses_fallback(no_llm):
    evidence = evidence_extractor.extract_evidence(SAMPLE_DATA)
    assert evidence == evidence_extractor._fallback_evidence(SAMPLE_DATA)


def test_extract_with_llm_parses_json(fake_llm):
    payload = '[{"claim": "Temps rose", "source_id": 1, "quote": "rose 1.2", "confidence": "high"}]'
    llm = fake_llm(FakeLLM(content=payload))
    evidence = evidence_extractor.extract_evidence(SAMPLE_DATA)
    assert llm.calls == 1
    assert evidence[0]["claim"] == "Temps rose"
    assert evidence[0]["source_id"] == 1


def test_extract_retries_then_falls_back(fake_llm, monkeypatch):
    monkeypatch.setattr(evidence_extractor.time, "sleep", lambda s: None)
    llm = fake_llm(FakeLLM(fail=True))
    evidence = evidence_extractor.extract_evidence(SAMPLE_DATA, retries=2, delay=0)
    assert llm.calls == 2
    assert evidence == evidence_extractor._fallback_evidence(SAMPLE_DATA)


def test_extract_empty_data_short_circuits(monkeypatch):
    called = []
    monkeypatch.setattr(evidence_extractor, "_get_llm", lambda: called.append(1))
    assert evidence_extractor.extract_evidence([]) == []
    assert called == []  # LLM init not even attempted


# --- render_evidence_table ----------------------------------------------

def test_render_lists_numbered_sources_with_urls():
    rendered = evidence_extractor.render_evidence_table([], SAMPLE_DATA)
    assert "[1] Climate Report (https://example.com/climate)" in rendered
    assert "[2] Ocean Warming Study (https://example.org/ocean)" in rendered
    assert "No structured evidence extracted" in rendered


def test_render_includes_evidence_rows():
    evidence = [{
        "claim": "Temperatures rose",
        "source_id": 1,
        "quote": "rose 1.2 degrees",
        "confidence": "high",
    }]
    rendered = evidence_extractor.render_evidence_table(evidence, SAMPLE_DATA)
    assert "Evidence table:" in rendered
    assert "- Temperatures rose" in rendered
    assert 'Quote: "rose 1.2 degrees"' in rendered
    assert "(source [1], confidence: high)" in rendered


def test_render_empty_inputs_returns_empty_string():
    assert evidence_extractor.render_evidence_table([], []) == ""


def test_render_prompt_contains_numbered_sources():
    prompt = evidence_extractor.build_evidence_prompt(SAMPLE_DATA)
    assert "[1] Climate Report" in prompt
    assert "[2] Ocean Warming Study" in prompt
    assert "JSON array" in prompt
