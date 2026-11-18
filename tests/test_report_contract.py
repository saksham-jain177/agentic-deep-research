"""Tests for the versioned report contract (report_contract.py, ROADMAP item 21).

The eval harness, the cost dashboard and the exporters all parse the report
dict the pipeline emits; silent drift there is how a benchmark starts lying.
The contract pins the shape:

* ``SCHEMA_VERSION`` is stamped on every report dict the pipeline emits.
* ``validate`` returns a violation list for missing required keys,
  undocumented keys, wrong sub-shapes and a stale or missing version.
* The end-to-end test runs the offline mock pipeline and validates its
  emitted report against the contract - so ANY shape change (a key added,
  removed, or retyped anywhere in the pipeline) fails here until the
  contract is updated and the version bumped. That is the "fails on an
  undocumented shape change" guarantee.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import report_contract  # noqa: E402
from report_contract import SCHEMA_VERSION, validate  # noqa: E402


def _valid_report():
    return {
        "schema_version": SCHEMA_VERSION,
        "sections": [{"title": "Key Findings", "content": "Body text [1]."}],
        "references": ["[1] Source. Retrieved from https://example.com"],
        "metadata": {
            "model": "mock",
            "language": "english",
            "writing_style": "academic",
            "citation_format": "APA",
        },
        "verification": [{"verdict": "supported", "coverage": 1.0}],
        "contradictions": [],
    }


class TestValidate:
    def test_a_well_formed_report_has_no_violations(self):
        assert validate(_valid_report()) == []

    def test_missing_required_key_is_a_violation(self):
        report = _valid_report()
        del report["references"]
        violations = validate(report)
        assert any("references" in v for v in violations)

    def test_undocumented_top_level_key_is_a_violation(self):
        # The guard this module exists for: a key added anywhere in the
        # pipeline without updating the contract fails the suite.
        report = _valid_report()
        report["word_count"] = 1234
        violations = validate(report)
        assert any("word_count" in v and "undocumented" in v for v in violations)

    def test_missing_schema_version_is_a_violation(self):
        report = _valid_report()
        del report["schema_version"]
        assert any("schema_version" in v for v in validate(report))

    def test_stale_schema_version_is_a_violation(self):
        report = _valid_report()
        report["schema_version"] = "0.9"
        violations = validate(report)
        assert any("schema_version" in v and "0.9" in v for v in violations)

    def test_section_with_wrong_shape_is_a_violation(self):
        report = _valid_report()
        report["sections"] = [{"title": "S"}]  # content missing
        assert any("sections" in v for v in validate(report))

    def test_section_with_undocumented_key_is_a_violation(self):
        report = _valid_report()
        report["sections"] = [{"title": "S", "content": "c", "score": 0.9}]
        assert any("score" in v for v in validate(report))

    def test_section_with_optional_verification_is_allowed(self):
        report = _valid_report()
        report["sections"][0]["verification"] = {"verdict": "supported"}
        assert validate(report) == []

    def test_events_count_is_an_allowed_optional_key(self):
        report = _valid_report()
        report["events"] = 9
        assert validate(report) == []

    def test_references_must_be_strings(self):
        report = _valid_report()
        report["references"] = [{"number": 1, "text": "x"}]
        assert any("references" in v for v in validate(report))

    def test_metadata_missing_documented_keys_is_a_violation(self):
        report = _valid_report()
        report["metadata"] = {"model": "mock"}
        violations = validate(report)
        assert any("writing_style" in v for v in violations)

    def test_error_key_is_optional_and_allowed(self):
        report = _valid_report()
        report["error"] = "partial failure"
        assert validate(report) == []

    def test_non_dict_report_is_a_violation_not_a_crash(self):
        assert validate(["not", "a", "dict"])  # non-empty violation list


class TestPipelineEmitsTheContractedShape:
    """The end-to-end guarantee: the offline mock pipeline's emitted report
    validates clean. Any undocumented shape change anywhere upstream fails
    this test."""

    def test_mock_pipeline_report_validates_and_is_versioned(self):
        import stream_cli

        report = stream_cli._consume(stream_cli._mock_stream(deep=True, words=400))
        violations = validate(report)
        assert violations == [], f"pipeline report violates the contract: {violations}"
        assert report["schema_version"] == SCHEMA_VERSION

    def test_empty_stream_still_produces_a_versioned_report(self):
        # An empty stream yields the versioned skeleton, but it is NOT a
        # complete report: metadata is empty, and the contract honestly
        # flags that instead of pretending the shape is fine.
        import draft_agent

        report = draft_agent.collect_streamed_report(iter([]))
        assert report["schema_version"] == SCHEMA_VERSION
        assert report["events"] == 0
        violations = validate(report)
        assert violations, "an empty report must not validate as complete"
        assert all("metadata" in v for v in violations)


class TestContractHygiene:
    def test_schema_version_is_a_plain_version_string(self):
        parts = SCHEMA_VERSION.split(".")
        assert len(parts) == 2
        assert all(p.isdigit() for p in parts)

    def test_documented_required_keys_match_the_pipeline(self):
        # The contract documents what the pipeline emits - if these sets drift
        # from reality, the end-to-end test above catches it; this pins the
        # contract's own constants so a typo cannot silently narrow the guard.
        assert report_contract.REQUIRED_KEYS == {
            "sections", "references", "metadata", "verification", "contradictions",
        }
        assert "error" in report_contract.OPTIONAL_KEYS
        assert "schema_version" in report_contract.OPTIONAL_KEYS
        assert "events" in report_contract.OPTIONAL_KEYS
        assert "verification" in report_contract.SECTION_OPTIONAL_KEYS
