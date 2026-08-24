"""Offline tests for multilingual verification (Tier 4).

All LLM calls are mocked; no API keys required.
"""

from unittest.mock import patch, MagicMock

import pytest

import contradiction_detector
import verification_node
from verification_node import (
    build_verification_prompt,
    resolve_verification_language,
    resolve_contradiction_language,
    verify_section,
    verify_report,
)
from contradiction_detector import (
    build_contradiction_prompt,
    detect_contradictions,
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


VERDICT_JSON = (
    '{"verdict": "supported", "unsupported_claims": [], '
    '"notes": "razonamiento en español"}'
)


class TestLanguageResolution:
    def test_explicit_arg_wins(self, monkeypatch):
        monkeypatch.setenv("VERIFICATION_LANGUAGE", "german")
        assert resolve_verification_language("spanish") == "spanish"

    def test_env_var_used_when_no_arg(self, monkeypatch):
        monkeypatch.setenv("VERIFICATION_LANGUAGE", "french")
        assert resolve_verification_language(None) == "french"

    def test_none_when_neither_set(self, monkeypatch):
        monkeypatch.delenv("VERIFICATION_LANGUAGE", raising=False)
        assert resolve_verification_language(None) is None

    def test_blank_values_fall_through(self, monkeypatch):
        monkeypatch.setenv("VERIFICATION_LANGUAGE", "   ")
        assert resolve_verification_language("") is None

    def test_contradiction_resolver_independent(self, monkeypatch):
        monkeypatch.setenv("VERIFICATION_LANGUAGE", "spanish")
        monkeypatch.delenv("CONTRADICTION_LANGUAGE", raising=False)
        assert resolve_contradiction_language(None) is None
        monkeypatch.setenv("CONTRADICTION_LANGUAGE", "chinese")
        assert resolve_contradiction_language(None) == "chinese"


class TestPromptLanguage:
    def test_no_language_keeps_english_prompt(self, sample_data):
        prompt = build_verification_prompt("T", "text [1].", sample_data)
        assert "spanish" not in prompt.lower().replace("sources", "")
        assert "Write your reasoning" not in prompt

    def test_spanish_instruction_present(self, sample_data):
        prompt = build_verification_prompt("T", "text [1].", sample_data,
                                           language="spanish")
        assert "spanish" in prompt
        assert 'MUST still use' in prompt
        # Enum contract stays English.
        assert '"supported"' in prompt and '"partially_supported"' in prompt

    def test_contradiction_prompt_language(self, sample_data):
        plain = build_contradiction_prompt(sample_data)
        spanish = build_contradiction_prompt(sample_data, language="spanish")
        assert "claim_a, claim_b and topic in spanish" in spanish
        assert "claim_a, claim_b and topic in" not in plain
        assert '"high"|"medium"|"low"' in spanish


class TestCanonicalEnumsPreserved:
    def test_verdict_normalization_ignores_language(self, sample_data):
        """A judge answering with a translated verdict still normalizes to
        canonical English or falls back — never leaks a foreign enum."""
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = _resp(
            '{"verdict": "soportado", "unsupported_claims": [], "notes": "n"}'
        )
        with patch.object(verification_node, "_get_llm", lambda: fake_llm):
            verdict = verify_section("T", "uncited claim.", sample_data,
                                     language="spanish")
        assert verdict["verdict"] == "unsupported"  # heuristic fallback (0% coverage)
        assert verdict["verdict"] in verification_node.VALID_VERDICTS

    def test_english_enum_with_foreign_notes_accepted(self, sample_data):
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = _resp(VERDICT_JSON)
        with patch.object(verification_node, "_get_llm", lambda: fake_llm):
            verdict = verify_section("T", "text [1].", sample_data,
                                     language="spanish")
        assert verdict["verdict"] == "supported"
        assert verdict["notes"] == "razonamiento en español"

    def test_severity_stays_canonical(self, sample_data):
        items = [{"claim_a": "a", "source_id_a": 1, "claim_b": "b",
                  "source_id_b": 2, "topic": "tema", "severity": "alto"}]
        normalized = contradiction_detector._normalize_contradictions(items, 2)
        assert normalized[0]["severity"] == "medium"  # invalid -> default
        assert normalized[0]["topic"] == "tema"       # content stays as-is


class TestPassThrough:
    def test_verify_section_threads_language_into_prompt(self, sample_data):
        captured = {}

        def fake_build(title, text, data, language=None):
            captured["language"] = language
            return "PROMPT"

        fake_llm = MagicMock()
        fake_llm.invoke.return_value = _resp(VERDICT_JSON)
        with patch.object(verification_node, "_get_llm", lambda: fake_llm), \
             patch.object(verification_node, "build_verification_prompt",
                          side_effect=fake_build):
            verify_section("T", "text", sample_data, language="german")
        assert captured["language"] == "german"

    def test_verify_report_passes_language_to_sections(self, sample_data):
        seen = []
        sections = [{"title": "S", "content": "c [1]."}]
        with patch.object(verification_node, "verify_section",
                          side_effect=lambda t, x, d, language=None: seen.append(language) or {}):
            verify_report(sections, sample_data, language="french")
        assert seen == ["french"]

    def test_detect_contradictions_threads_language(self, sample_data):
        captured = {}
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = _resp("[]")

        def fake_build(d, language=None):
            captured["language"] = language
            return "PROMPT"

        with patch.object(contradiction_detector, "_get_llm", lambda: fake_llm), \
             patch.object(contradiction_detector, "build_contradiction_prompt",
                          side_effect=fake_build):
            detect_contradictions(sample_data, language="chinese")
        assert captured["language"] == "chinese"


class TestFallbackUnchanged:
    def test_heuristic_verdict_without_llm_or_language(self, sample_data, monkeypatch):
        monkeypatch.delenv("VERIFICATION_LANGUAGE", raising=False)
        verdict = verify_section("T", "fully cited text [1].\n\nmore [2].",
                                 sample_data)
        assert verdict["verdict"] == "supported"
        assert "Heuristic verdict" in verdict["notes"]
        assert verdict["unsupported_claims"] == []

    def test_detect_contradictions_heuristic_fallback_without_llm(
            self, sample_data, monkeypatch):
        monkeypatch.delenv("CONTRADICTION_LANGUAGE", raising=False)
        with patch.object(contradiction_detector, "_get_llm", lambda: None), \
             patch.object(contradiction_detector,
                          "detect_contradictions_heuristic", return_value=[]) as h:
            result = detect_contradictions(sample_data)
        assert result == []
        assert h.called

    def test_empty_language_same_behavior_as_before(self, sample_data):
        """No language set -> prompt identical to the pre-Tier-4 shape."""
        base = build_verification_prompt("T", "x [1]", sample_data)
        explicit = build_verification_prompt("T", "x [1]", sample_data,
                                             language=None)
        assert base == explicit
