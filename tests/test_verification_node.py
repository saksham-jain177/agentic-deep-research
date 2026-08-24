"""Offline tests for the verification node (LLM-as-a-Judge pass).

All LLM calls are mocked; no API keys required.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

import verification_node
from verification_node import (
    build_verification_prompt,
    compute_citations_coverage,
    heuristic_verdict,
    verify_section,
    verify_report,
    render_verification_summary,
    _normalize_verdict,
    _parse_verdict_json,
)


@pytest.fixture
def sample_data():
    return [
        {
            "title": "Climate report",
            "url": "https://example.com/climate",
            "content": "Global yields of wheat fall by 6% per degree of warming.",
        },
        {
            "title": "Agri study",
            "url": "https://example.org/agri",
            "content": "Farmers adapt via drought-resistant cultivars.",
        },
    ]


@pytest.fixture
def cited_section():
    return (
        "Wheat yields drop 6% per degree of warming [1].\n\n"
        "Adaptation relies on drought-resistant cultivars [2]."
    )


@pytest.fixture
def uncited_section():
    return "Wheat yields collapse everywhere.\n\nNobody knows how to adapt."


class TestCitationsCoverage:
    def test_all_paragraphs_cited(self, cited_section):
        assert compute_citations_coverage(cited_section) == 1.0

    def test_no_paragraphs_cited(self, uncited_section):
        assert compute_citations_coverage(uncited_section) == 0.0

    def test_partial_coverage(self):
        text = "Claim one [1].\n\nClaim two without citation."
        assert compute_citations_coverage(text) == pytest.approx(0.5)

    def test_empty_text_counts_as_covered(self):
        assert compute_citations_coverage("") == 1.0


class TestHeuristicVerdict:
    def test_high_coverage_supported(self, uncited_section):
        v = heuristic_verdict("Analysis", "Cited claim [1]. Another [2].", None)
        # coverage computed internally when None passed
        assert v["verdict"] == "supported"
        assert v["citations_coverage"] == 1.0

    def test_zero_coverage_unsupported(self, uncited_section):
        v = heuristic_verdict("Analysis", uncited_section)
        assert v["verdict"] == "unsupported"

    def test_partial(self):
        v = heuristic_verdict("S", "One [1].\n\nTwo.")
        assert v["verdict"] == "partially_supported"
        assert "Heuristic" in v["notes"]

    def test_never_lists_claims(self, uncited_section):
        v = heuristic_verdict("S", uncited_section)
        assert v["unsupported_claims"] == []


class TestPrompt:
    def test_prompt_contains_sources_and_section(self, sample_data, cited_section):
        prompt = build_verification_prompt("Key Findings", cited_section, sample_data)
        assert "[1] Climate report" in prompt
        assert "[2] Agri study" in prompt
        assert "Key Findings" in prompt
        assert "JSON" in prompt


class TestParsing:
    def test_parse_plain_json(self):
        obj = _parse_verdict_json('{"verdict": "supported", "unsupported_claims": []}')
        assert obj["verdict"] == "supported"

    def test_parse_fenced_json_with_think(self):
        raw = '<think>hmm</think>```json\n{"verdict": "unsupported"}\n```'
        assert _parse_verdict_json(raw)["verdict"] == "unsupported"

    def test_parse_rejects_non_object(self):
        with pytest.raises(ValueError):
            _parse_verdict_json('["not an object"]')

    def test_normalize_drops_bad_verdict(self, cited_section):
        assert _normalize_verdict({"verdict": "excellent"}, "S", 1.0) is None

    def test_normalize_coerces_claims(self, cited_section):
        v = _normalize_verdict(
            {"verdict": "Partially Supported", "unsupported_claims": ["x", " ", 42], "notes": "n"},
            "S",
            0.5,
        )
        assert v["verdict"] == "partially_supported"
        assert v["unsupported_claims"] == ["x", "42"]
        assert v["citations_coverage"] == 0.5


class TestVerifySectionWithMockLLM:
    def _mock_llm(self, payload):
        resp = MagicMock()
        resp.content = json.dumps(payload)
        llm = MagicMock()
        llm.invoke.return_value = resp
        return llm

    def test_llm_verdict_used(self, sample_data, cited_section):
        payload = {
            "verdict": "partially_supported",
            "unsupported_claims": ["half the harvest is gone"],
            "notes": "one claim exaggerated",
        }
        with patch.object(verification_node, "_get_llm", return_value=self._mock_llm(payload)):
            v = verify_section("Key Findings", cited_section, sample_data)
        assert v["verdict"] == "partially_supported"
        assert v["unsupported_claims"] == ["half the harvest is gone"]
        assert v["citations_coverage"] == 1.0

    def test_invalid_llm_output_falls_back(self, sample_data, uncited_section):
        resp = MagicMock()
        resp.content = "I am not json at all"
        llm = MagicMock()
        llm.invoke.return_value = resp
        with patch.object(verification_node, "_get_llm", return_value=llm), \
             patch.object(verification_node.time, "sleep"):
            v = verify_section("Analysis", uncited_section, sample_data)
        assert v["verdict"] == "unsupported"
        assert "Heuristic" in v["notes"]

    def test_no_llm_uses_heuristic(self, sample_data, cited_section):
        with patch.object(verification_node, "_get_llm", return_value=None):
            v = verify_section("Abstract", cited_section, sample_data)
        assert v["verdict"] == "supported"


class TestVerifyReportAndSummary:
    def test_verify_report_covers_every_section(self, sample_data):
        sections = [
            {"title": "A", "content": "cited [1]"},
            {"title": "B", "content": "not cited"},
        ]
        with patch.object(verification_node, "_get_llm", return_value=None):
            results = verify_report(sections, sample_data)
        assert len(results) == 2
        assert {r["section"] for r in results} == {"A", "B"}

    def test_crashing_verifier_falls_back_per_section(self, sample_data):
        sections = [{"title": "X", "content": "text [1]"}]
        with patch.object(verification_node, "verify_section", side_effect=RuntimeError("boom")):
            results = verify_report(sections, sample_data)
        assert len(results) == 1
        assert results[0]["verdict"] in {"supported", "partially_supported", "unsupported"}

    def test_summary_renders_markdown(self):
        verifications = [
            {"section": "A", "verdict": "supported", "unsupported_claims": [], "citations_coverage": 1.0},
            {
                "section": "B",
                "verdict": "partially_supported",
                "unsupported_claims": ["the moon is cheese"],
                "citations_coverage": 0.4,
            },
        ]
        md = render_verification_summary(verifications)
        assert md.startswith("## Verification Summary")
        assert "**A**: supported" in md
        assert "**B**: partially_supported" in md
        assert '"the moon is cheese"' in md

    def test_empty_verifications_render_empty(self):
        assert render_verification_summary([]) == ""
