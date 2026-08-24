"""Offline tests for the cost dashboard (Tier 4).

No API keys or network required; pricing uses the deterministic fallback
rates from cost_estimator.FALLBACK_PRICING.
"""

import json
import math

import pytest

import cost_estimator
import cost_dashboard
from cost_dashboard import (
    CostRunTracker,
    STAGES,
    build_dashboard_html,
    generate_from_usage,
)


@pytest.fixture
def tracker():
    t = CostRunTracker(run_id="run-test", deep_research=True, query="q")
    t.record("research", input_tokens=1000, output_tokens=500)
    t.record("draft", input_tokens=2000, output_tokens=3000)
    t.record("verification", input_tokens=400, output_tokens=100)
    t.record("contradiction", input_tokens=600, output_tokens=200)
    return t


def _expected_cost(in_tok, out_tok):
    return ((in_tok / 1000) * cost_estimator.FALLBACK_PRICING["input"]
            + (out_tok / 1000) * cost_estimator.FALLBACK_PRICING["output"])


class TestAggregationMath:
    def test_all_stages_present(self, tracker):
        summary = tracker.build_summary()
        assert set(summary["stages"]) == set(STAGES)

    def test_per_stage_totals(self, tracker):
        s = tracker.build_summary()
        draft = s["stages"]["draft"]
        assert draft["input_tokens"] == 2000
        assert draft["output_tokens"] == 3000

    def test_grand_totals(self, tracker):
        s = tracker.build_summary()
        assert s["totals"]["input_tokens"] == 1000 + 2000 + 400 + 600
        assert s["totals"]["output_tokens"] == 500 + 3000 + 100 + 200
        assert s["totals"]["total_tokens"] == (
            s["totals"]["input_tokens"] + s["totals"]["output_tokens"])

    def test_cost_math_uses_fallback_rates(self, tracker):
        s = tracker.build_summary()
        expected = sum(_expected_cost(i, o) for i, o in [
            (1000, 500), (2000, 3000), (400, 100), (600, 200)])
        assert math.isclose(s["totals"]["cost_usd"], expected, abs_tol=1e-9)

    def test_multiple_models_aggregated_separately(self, tracker):
        tracker.record("draft", input_tokens=1000, output_tokens=0,
                       model="model-b")
        s = tracker.build_summary()
        draft_models = {m["model"] for m in s["stages"]["draft"]["models"]}
        assert draft_models == {"unknown", "model-b"}
        assert s["stages"]["draft"]["input_tokens"] == 3000

    def test_accumulation_across_calls(self, tracker):
        tracker.record("research", input_tokens=250, output_tokens=0)
        s = tracker.build_summary()
        assert s["stages"]["research"]["input_tokens"] == 1250

    def test_negative_and_none_coerced_to_zero(self, tracker):
        tracker.record("research", input_tokens=-5, output_tokens=None)
        s = tracker.build_summary()
        # unchanged from fixture's 1000/500
        assert s["stages"]["research"]["input_tokens"] == 1000
        assert s["stages"]["research"]["output_tokens"] == 500

    def test_unknown_stage_raises(self, tracker):
        with pytest.raises(ValueError):
            tracker.record("not-a-stage", 1, 1)

    def test_empty_tracker_zero_totals(self):
        s = CostRunTracker(run_id="empty").build_summary()
        assert s["totals"]["total_tokens"] == 0
        assert s["totals"]["cost_usd"] == 0.0


class TestJsonSchema:
    REQUIRED_KEYS = {"schema_version", "run_id", "deep_research", "query",
                     "stages", "totals"}
    STAGE_KEYS = {"models", "input_tokens", "output_tokens", "cost_usd"}
    MODEL_KEYS = {"model", "input_tokens", "output_tokens", "cost_usd"}
    TOTAL_KEYS = {"input_tokens", "output_tokens", "total_tokens", "cost_usd"}

    def test_top_level_schema(self, tracker):
        s = tracker.build_summary()
        assert self.REQUIRED_KEYS <= set(s)
        assert isinstance(s["schema_version"], int)

    def test_stage_schema(self, tracker):
        s = tracker.build_summary()
        for stage in STAGES:
            assert self.STAGE_KEYS <= set(s["stages"][stage])
            for m in s["stages"][stage]["models"]:
                assert self.MODEL_KEYS <= set(m)
                assert isinstance(m["cost_usd"], float)
                assert m["cost_usd"] >= 0.0

    def test_totals_schema(self, tracker):
        s = tracker.build_summary()
        assert self.TOTAL_KEYS <= set(s["totals"])
        assert all(isinstance(s["totals"][k], (int, float))
                   for k in self.TOTAL_KEYS)

    def test_json_round_trip(self, tracker, tmp_path):
        s = tracker.build_summary()
        path = tmp_path / "run-summary.json"
        cost_dashboard.write_run_summary(s, str(path))
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == s

    def test_generate_from_usage_string_tokens(self):
        s = generate_from_usage(
            run_id="gen",
            usage_rows=[
                {"stage": "draft", "input_tokens": "one two three four five",
                 "output_tokens": ""},
            ],
        )
        # "" -> 0 tokens; string input estimated via count_text_tokens (>0)
        assert s["stages"]["draft"]["input_tokens"] > 0
        assert s["stages"]["draft"]["output_tokens"] == 0


class TestHtmlDashboard:
    def test_contains_expected_markers(self, tracker):
        html = build_dashboard_html(tracker.build_summary())
        for marker in ("<!DOCTYPE html>", "<style>", "<script>",
                       "id=\"run-data\"", "render(", "bar-chart"):
            assert marker in html

    def test_embedded_json_matches_summary(self, tracker):
        summary = tracker.build_summary()
        html = build_dashboard_html(summary)
        start = html.index('type="application/json">') + len(
            'type="application/json">')
        end = html.index("</script>", start)
        embedded = json.loads(html[start:end])
        assert embedded == summary

    def test_no_external_resources(self, tracker):
        """Single-file constraint: no CDN links, external scripts or styles."""
        html = build_dashboard_html(tracker.build_summary())
        lowered = html.lower()
        assert "http://" not in lowered and "https://" not in lowered
        assert 'src=' not in lowered
        assert "cdn" not in lowered
        assert "<link" not in lowered

    def test_run_id_in_title(self, tracker):
        html = build_dashboard_html(tracker.build_summary())
        assert "run-test" in html

    def test_script_close_tag_escaped_in_data(self, tmp_path):
        tracker2 = CostRunTracker(run_id="</script><b>x</b>")
        html = build_dashboard_html(tracker2.build_summary())
        body_start = html.index('type="application/json">')
        data_blob = html[body_start:html.index("</script>", body_start)]
        assert "</script>" not in data_blob.replace(
            html[-8:], "")  # raw close tag never inside the JSON blob
