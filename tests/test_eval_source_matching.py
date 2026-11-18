"""Regression tests for eval findings F5 and F8.

F5 - source matching was positional/substring: a same-domain substring match
returned the FIRST source with that domain (wrong page when two share one),
and a URL that matched nothing fell back to the reference number's position
in the source list, grading claims against an arbitrary page. Both credit a
claim to a source the report may not have used - an over-count, the one
failure mode a benchmark must not have.

F8 - Cohen's kappa was computed by ``eval calibrate`` but never enforced in
the run path, so the calibration gate was decorative. The run path now
refuses to score with a judge below the gate unless explicitly overridden,
records the calibration in run metadata, and the rendered report marks an
uncalibrated run loudly.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.citations import extract_references, find_reference_text, parse_report  # noqa: E402
from eval.report import calibration_notice, render_markdown  # noqa: E402
from eval.runner import RunMetadata, build_payload  # noqa: E402


def _ref(number, url):
    return extract_references(f"[{number}] Title. Retrieved from {url}")[number]


class TestF5SourceMatching:
    SOURCES = [
        {"url": "https://www.noaa.gov/water", "content": "noaa water page"},
        {"url": "https://www.noaa.gov/climate", "content": "noaa climate page"},
        {"url": "https://www.usgs.gov/altitude", "content": "usgs altitude page"},
    ]

    def test_exact_url_match_after_normalisation(self):
        # Scheme, www and trailing slash differences must not break the match.
        ref = _ref(1, "http://noaa.gov/water/")
        text = find_reference_text(1, self.SOURCES, {1: ref})
        assert text == "noaa water page"

    def test_unique_domain_match_resolves(self):
        # One source carries usgs.gov: the domain match is unambiguous.
        ref = _ref(3, "https://usgs.gov/altitude-plus")  # not an exact URL match
        text = find_reference_text(3, self.SOURCES, {3: ref})
        assert text == "usgs altitude page"

    def test_ambiguous_domain_returns_empty_not_first_match(self):
        # Two noaa.gov sources: picking the first could credit a climate claim
        # to the water page. Ambiguity must fail the pair, not guess.
        ref = _ref(1, "https://noaa.gov/some-other-page")
        text = find_reference_text(1, self.SOURCES, {1: ref})
        assert text == ""

    def test_url_match_failure_never_falls_back_to_position(self):
        # Reference 3 has a URL that matches nothing. The old code returned
        # sources[2] ("usgs altitude page") purely because 3 <= len(sources).
        ref = _ref(3, "https://example.org/unrelated")
        text = find_reference_text(3, self.SOURCES, {3: ref})
        assert text == ""

    def test_positional_fallback_only_without_url(self):
        # A reference with NO URL may still use its position: the agent
        # numbered sources in order and emitted no link to disambiguate.
        refs = extract_references("[2] Bare reference without a link")
        text = find_reference_text(2, self.SOURCES, refs)
        assert text == "noaa climate page"

    def test_positional_fallback_out_of_range_is_empty(self):
        refs = extract_references("[9] Bare reference without a link")
        assert find_reference_text(9, self.SOURCES, refs) == ""

    def test_unknown_reference_number_is_empty(self):
        assert find_reference_text(7, self.SOURCES, {}) == ""

    def test_end_to_end_wrong_source_credit_is_gone(self):
        # The failure the fix exists for: a report cites [2] with a real URL,
        # the URL is absent from the source list, and the old positional
        # fallback handed back sources[1]'s content - grading the claim
        # against a page the report never cited.
        report = (
            "## Findings\n\nThe altitude effect is real [2].\n\n"
            "## References\n\n[2] NOAA. Climate. Retrieved from https://noaa.gov/missing-page\n"
        )
        parsed = parse_report(report)
        assert parsed.claims, "claim should parse"
        text = find_reference_text(2, self.SOURCES, parsed.references)
        assert text == ""


class TestF8CalibrationGate:
    def _labels(self, path, pairs):
        with open(path, "w", encoding="utf-8") as handle:
            for human, model in pairs:
                handle.write(json.dumps({"human": human, "model": model, "kind": "support"}) + "\n")

    def test_metadata_carries_calibration(self):
        metadata = RunMetadata(
            mode="replay",
            judge_name="heuristic",
            judge_calibration={"n": 40, "raw_agreement": 90.0, "cohen_kappa": 0.75},
        )
        payload = build_payload([], metadata)
        assert payload["metadata"]["judge_calibration"]["cohen_kappa"] == 0.75

    def test_metadata_without_calibration_is_none(self):
        metadata = RunMetadata(mode="replay", judge_name="heuristic")
        payload = build_payload([], metadata)
        assert payload["metadata"]["judge_calibration"] is None

    def test_report_warns_on_uncalibrated_run(self):
        payload = {"metadata": {"judge_calibration": None}, "metrics": {}, "by_tier": {}}
        markdown = render_markdown(payload)
        assert "UNCALIBRATED" in markdown
        assert "not publishable" in markdown

    def test_report_states_calibration_when_present(self):
        payload = {
            "metadata": {
                "judge_calibration": {"n": 40, "raw_agreement": 90.0, "cohen_kappa": 0.75}
            },
            "metrics": {},
            "by_tier": {},
        }
        markdown = render_markdown(payload)
        assert "0.75" in markdown
        assert "40 human labelled pairs" in markdown
        assert "UNCALIBRATED" not in markdown

    def test_report_flags_below_gate_calibration(self):
        payload = {
            "metadata": {
                "judge_calibration": {"n": 12, "raw_agreement": 55.0, "cohen_kappa": 0.2}
            },
            "metrics": {},
            "by_tier": {},
        }
        markdown = render_markdown(payload)
        assert "BELOW the 0.6 gate" in markdown

    def test_calibration_notice_helper_direct(self):
        assert "UNCALIBRATED" in calibration_notice({"judge_calibration": None})
        passing = calibration_notice(
            {"judge_calibration": {"n": 10, "raw_agreement": 95.0, "cohen_kappa": 0.9}}
        )
        assert "0.9" in passing and "UNCALIBRATED" not in passing
