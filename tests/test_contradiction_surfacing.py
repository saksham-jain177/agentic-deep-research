"""Offline tests for contradiction surfacing (contradiction_detector).

All LLM calls are mocked; no API keys required.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

import contradiction_detector
from contradiction_detector import (
    build_contradiction_prompt,
    detect_contradictions,
    detect_contradictions_heuristic,
    render_contradictions_block,
    _normalize_contradictions,
    _parse_contradictions_json,
)


@pytest.fixture
def conflicting_sources():
    return [
        {
            "title": "Report A",
            "url": "https://example.com/a",
            "content": "Global wheat yields rose by 8% in 2024 according to the ministry.",
        },
        {
            "title": "Report B",
            "url": "https://example.org/b",
            "content": "Independent audits show wheat yields dropped 3% in 2024.",
        },
        {
            "title": "Unrelated",
            "url": "https://example.net/c",
            "content": "Quantum computing research continues at several universities worldwide.",
        },
    ]


class TestPrompt:
    def test_prompt_contains_sources_and_contract(self, conflicting_sources):
        prompt = build_contradiction_prompt(conflicting_sources)
        assert "[1] Report A" in prompt
        assert "[2] Report B" in prompt
        assert "JSON" in prompt
        assert "cannot both be true" in prompt


class TestParsing:
    def test_parse_plain_array(self):
        items = _parse_contradictions_json('[{"claim_a": "x", "claim_b": "y"}]')
        assert len(items) == 1

    def test_parse_fenced_with_think(self):
        raw = '<think>hmm</think>```json\n[{"claim_a": "x"}]\n```'
        assert _parse_contradictions_json(raw)[0]["claim_a"] == "x"

    def test_parse_rejects_non_array(self):
        with pytest.raises(ValueError):
            _parse_contradictions_json('{"not": "an array"}')


class TestNormalize:
    def test_drops_out_of_range_source_ids(self):
        out = _normalize_contradictions(
            [{"claim_a": "a", "source_id_a": 1, "claim_b": "b", "source_id_b": 9}],
            num_sources=2,
        )
        assert out == []

    def test_drops_missing_claims(self):
        out = _normalize_contradictions(
            [{"source_id_a": 1, "source_id_b": 2}], num_sources=2
        )
        assert out == []

    def test_coerces_severity_default(self):
        out = _normalize_contradictions(
            [{"claim_a": "a", "source_id_a": "1", "claim_b": "b",
              "source_id_b": "2", "severity": "CRITICAL"}],
            num_sources=2,
        )
        assert out[0]["severity"] == "medium"

    def test_keeps_valid_entry(self):
        out = _normalize_contradictions(
            [{"claim_a": "yields rose", "source_id_a": 1, "claim_b": "yields fell",
              "source_id_b": 2, "topic": "yield direction", "severity": "high"}],
            num_sources=2,
        )
        assert out == [{
            "claim_a": "yields rose", "source_id_a": 1,
            "claim_b": "yields fell", "source_id_b": 2,
            "topic": "yield direction", "severity": "high",
        }]


class TestHeuristicDetector:
    def test_flags_numeric_disagreement(self, conflicting_sources):
        out = detect_contradictions_heuristic(conflicting_sources)
        assert len(out) >= 1
        pair = {out[0]["source_id_a"], out[0]["source_id_b"]}
        assert pair <= {1, 2}
        assert all(c.get("heuristic") for c in out)

    def test_no_shared_topic_no_conflict(self):
        data = [
            {"content": "The mitochondria is the powerhouse of the biological cell."},
        ]
        assert detect_contradictions_heuristic(data) == []

    def test_empty_data(self):
        assert detect_contradictions_heuristic([]) == []

    def test_caps_at_ten(self):
        data = [
            {"content": f"Metric number {n} grew 5% this year overall."}
            for n in range(15)
        ]
        # all sentences share words and numbers -> many pairs, capped
        assert len(detect_contradictions_heuristic(data)) <= 10


class TestDetectWithMockLLM:
    def _mock_llm(self, payload):
        resp = MagicMock()
        resp.content = json.dumps(payload)
        llm = MagicMock()
        llm.invoke.return_value = resp
        return llm

    def test_llm_results_preferred(self, conflicting_sources):
        payload = [{
            "claim_a": "yields rose by 8%",
            "source_id_a": 1,
            "claim_b": "yields dropped 3%",
            "source_id_b": 2,
            "topic": "wheat yield trend",
            "severity": "high",
        }]
        with patch.object(
            contradiction_detector, "_get_llm", return_value=self._mock_llm(payload)
        ):
            out = detect_contradictions(conflicting_sources)
        assert len(out) == 1
        assert out[0]["severity"] == "high"
        assert "heuristic" not in out[0]

    def test_invalid_llm_output_falls_back(self, conflicting_sources):
        resp = MagicMock()
        resp.content = "total nonsense"
        llm = MagicMock()
        llm.invoke.return_value = resp
        with patch.object(contradiction_detector, "_get_llm", return_value=llm), \
             patch.object(contradiction_detector.time, "sleep"):
            out = detect_contradictions(conflicting_sources)
        assert len(out) >= 1
        assert all(c.get("heuristic") for c in out)

    def test_no_llm_uses_heuristic(self, conflicting_sources):
        with patch.object(contradiction_detector, "_get_llm", return_value=None):
            out = detect_contradictions(conflicting_sources)
        assert len(out) >= 1
        assert all(c.get("heuristic") for c in out)

    def test_empty_data_short_circuits(self):
        with patch.object(contradiction_detector, "_get_llm") as m:
            assert detect_contradictions([]) == []
        m.assert_not_called()


class TestRendering:
    def test_empty_renders_empty(self):
        assert render_contradictions_block([]) == ""

    def test_block_lists_both_claims_and_sources(self):
        block = render_contradictions_block([{
            "claim_a": "yields rose 8%", "source_id_a": 1,
            "claim_b": "yields fell 3%", "source_id_b": 2,
            "topic": "yield trend", "severity": "high",
        }])
        assert block.startswith("## Conflicting Evidence")
        assert "**HIGH**" in block
        assert "Source [1]" in block and "Source [2]" in block
        assert '"yields rose 8%"' in block and '"yields fell 3%"' in block
        assert "rather than averaged" in block

    def test_heuristic_entries_are_tagged(self):
        block = render_contradictions_block([{
            "claim_a": "a", "source_id_a": 1, "claim_b": "b", "source_id_b": 2,
            "topic": "t", "severity": "low", "heuristic": True,
        }])
        assert "(heuristic detection)" in block


class TestDraftIntegration:
    def test_draft_appends_conflicting_evidence_section(self, monkeypatch):
        import draft_agent

        class FakeLLM:
            content = json.dumps({"sections": [{"title": "S", "content": "Body [1]."}]})

            def invoke(self, messages):
                return self

        monkeypatch.setattr(draft_agent, "_get_llm", lambda: FakeLLM())
        monkeypatch.setattr(
            contradiction_detector, "detect_contradictions",
            lambda data, **kw: [{
                "claim_a": "rose", "source_id_a": 1, "claim_b": "fell",
                "source_id_b": 1, "topic": "trend", "severity": "medium",
            }],
        )
        raw = draft_agent.draft_answer(data=[{"content": "x", "url": "u"}])
        result = json.loads(raw)

        titles = [s["title"] for s in result["sections"]]
        assert "Conflicting Evidence" in titles
        assert len(result["contradictions"]) == 1

    def test_draft_survives_detector_crash(self, monkeypatch):
        import draft_agent

        class FakeLLM:
            content = json.dumps({"sections": [{"title": "S", "content": "Body [1]."}]})

            def invoke(self, messages):
                return self

        monkeypatch.setattr(draft_agent, "_get_llm", lambda: FakeLLM())

        def boom(*a, **kw):
            raise RuntimeError("detector down")

        monkeypatch.setattr(contradiction_detector, "detect_contradictions", boom)
        raw = draft_agent.draft_answer(data=[{"content": "x", "url": "u"}])
        result = json.loads(raw)
        assert "Conflicting Evidence" not in [s["title"] for s in result["sections"]]
