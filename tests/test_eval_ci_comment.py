"""Offline tests for the PR eval-comparison comment (scripts/eval_ci_comment.py).

These pin the two things that make the comment worth reading: that a real regression is named,
and that a change in *what was measured* is called out instead of being printed as a delta.
"""

import copy
import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "eval_ci_comment", os.path.join(ROOT, "scripts", "eval_ci_comment.py")
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _run(**overrides):
    metrics = {
        "citation_accuracy": 0.727,
        "content_available_rate": 1.0,
        "link_validity": None,
        "relevance_rate": 0.909,
        "support_rate": 0.8,
        "judge_failure_rate": 0.0,
        "effective_citations_per_report": 2.0,
        "claim_recall": 0.833,
        "uncited_claim_rate": 0.267,
        "abstention_rate_t4": 1.0,
        "false_abstention_rate": 0.0,
        "truncated_queries": 0,
        "latency_p50_s": 22.4,
        "latency_p95_s": 48.6,
        "mean_cost_usd": 0.00805,
        "n_queries": 40,
        "n_skipped": 36,
        "coverage_pct": 10.0,
        "n_pairs": 11,
    }
    metrics.update(overrides)
    return {
        "metadata": {"mode": "replay", "judge_name": "heuristic", "git_commit": "abc1234",
                     "started_at": "2026-11-18T12:00:00+00:00"},
        "metrics": metrics,
    }


def test_identical_runs_read_as_comparable_and_clean():
    body, worse = mod.build_comment(_run(), _run())
    assert mod.MARKER in body
    assert "**Comparable.**" in body
    assert worse == []
    assert "No headline metric moved" in body
    assert "| Metric | Baseline | Candidate | Delta |" in body


def test_citation_accuracy_regression_is_named():
    candidate = _run(citation_accuracy=0.5)
    body, worse = mod.build_comment(_run(), candidate)
    assert "Citation accuracy (all pairs)" in worse
    assert "Worse than `main`" in body


def test_replay_is_deterministic_so_any_adverse_move_is_named():
    """No tolerance: the fixtures and the heuristic judge are fixed, so 0.1 s is the code's doing."""
    body, worse = mod.build_comment(_run(), _run(latency_p50_s=22.5))
    assert worse == ["Latency p50 (s)"]
    assert "Worse than `main`" in body


def test_improvements_are_not_flagged():
    body, worse = mod.build_comment(_run(), _run(citation_accuracy=0.9, latency_p50_s=18.0))
    assert worse == []
    assert "No headline metric moved" in body


def test_recorded_rows_are_labelled_not_measured_rather_than_not_slower():
    body, _ = mod.build_comment(_run(), _run())
    assert "*not measured*, not as *not slower*" in body


def test_more_recorded_fixtures_stops_being_an_ablation():
    """Four scored queries vs six is a change in measurement, not in quality."""
    body, _ = mod.build_comment(_run(), _run(n_skipped=34, coverage_pct=15.0))
    assert "**Not comparable as an ablation.**" in body
    assert "4 of 40" in body and "6 of 40" in body


def test_partial_coverage_is_never_quoted_as_a_full_benchmark():
    body, _ = mod.build_comment(_run(), _run())
    assert "4 of 40 gold queries have replay recordings" in body


def test_posting_refuses_without_the_actions_environment(monkeypatch):
    for name in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_EVENT_PATH"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit):
        mod.post_comment("body")


def test_cli_writes_the_comment_to_a_file(tmp_path):
    base, cand = tmp_path / "base.json", tmp_path / "head.json"
    for path, payload in ((base, _run()), (cand, _run(citation_accuracy=0.5))):
        path.write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "comment.md"
    code = mod.main(["--baseline", str(base), "--candidate", str(cand), "--out", str(out)])
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "Worse than `main`" in text
    assert mod.MARKER in text


def test_committed_baseline_file_still_parses():
    """The workflow points at results/replay-goldset.json, so it must stay loadable."""
    path = os.path.join(ROOT, "results", "replay-goldset.json")
    with open(path, encoding="utf-8") as handle:
        shipped = json.load(handle)
    body, _ = mod.build_comment(shipped, copy.deepcopy(shipped))
    assert "**Comparable.**" in body
    assert mod.scored_queries(shipped["metrics"]) == 4
