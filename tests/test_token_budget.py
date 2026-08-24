"""Tests for token budget enforcement (draft_agent.enforce_token_budget)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import draft_agent
from draft_agent import enforce_token_budget, get_token_budget

RANK = {"low": 0, "medium": 1, "high": 2}


def _rows(spec):
    """Build evidence rows: spec is a list of (confidence, n) pairs."""
    rows = []
    for confidence, n in spec:
        for i in range(n):
            rows.append(
                {
                    "claim": f"Claim {confidence} {i}: " + "detail text " * 12,
                    "source_id": (i % 3) + 1,
                    "quote": f"quote {i}",
                    "confidence": confidence,
                }
            )
    return rows


@pytest.fixture
def data():
    return [
        {"title": f"Source {i}", "content": "content", "url": f"https://e.com/{i}"}
        for i in range(1, 4)
    ]


class TestGetTokenBudget:
    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("TOKEN_BUDGET_PER_REPORT", raising=False)
        assert get_token_budget() == draft_agent.DEFAULT_TOKEN_BUDGET_PER_REPORT

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TOKEN_BUDGET_PER_REPORT", "500")
        assert get_token_budget() == 500

    @pytest.mark.parametrize("bad", ["abc", "-5", "0", ""])
    def test_invalid_values_fall_back_to_default(self, monkeypatch, bad):
        monkeypatch.setenv("TOKEN_BUDGET_PER_REPORT", bad)
        assert get_token_budget() == draft_agent.DEFAULT_TOKEN_BUDGET_PER_REPORT


class TestEnforceTokenBudget:
    def test_under_budget_untouched(self, monkeypatch, data):
        monkeypatch.setenv("TOKEN_BUDGET_PER_REPORT", "100000")
        evidence = _rows([("high", 3), ("medium", 3), ("low", 3)])
        trimmed, info = enforce_token_budget(evidence, data, False, num_sections=2)
        assert info["dropped"] == 0
        assert [r["claim"] for r in trimmed] == [r["claim"] for r in evidence]

    def test_over_budget_drops_rows_until_fit(self, monkeypatch, data):
        evidence = _rows([("high", 4), ("medium", 4), ("low", 4)])
        # Budget just above the projection with NO low rows kept: the guard
        # must drop exactly the four low-confidence rows and then stop.
        no_low = [r for r in evidence if r["confidence"] != "low"]
        proj_no_low = draft_agent._projected_input_tokens(no_low, data, False, 2)
        budget = proj_no_low + 1
        monkeypatch.setenv("TOKEN_BUDGET_PER_REPORT", str(budget))

        trimmed, info = enforce_token_budget(evidence, data, False, num_sections=2)

        assert info["dropped"] > 0
        assert info["projected_tokens"] <= info["budget"]
        assert {r["confidence"] for r in info["dropped_rows"]} == {"low"}
        assert len(info["dropped_rows"]) == 4
        assert {r["confidence"] for r in trimmed} == {"medium", "high"}

    def test_low_confidence_trimmed_before_medium_and_high(self, monkeypatch, data):
        # Budget just above the projection with ONLY high rows kept: lows and
        # mediums must go first, highs must survive.
        evidence = _rows([("high", 2), ("medium", 2), ("low", 6)])
        highs_only = [r for r in evidence if r["confidence"] == "high"]
        proj_highs = draft_agent._projected_input_tokens(highs_only, data, False, 2)
        budget = proj_highs + 1
        monkeypatch.setenv("TOKEN_BUDGET_PER_REPORT", str(budget))

        trimmed, info = enforce_token_budget(evidence, data, False, num_sections=2)

        kept_ranks = sorted(RANK[r["confidence"]] for r in trimmed)
        assert kept_ranks == [2, 2]
        assert max(RANK[r["confidence"]] for r in info["dropped_rows"]) <= min(kept_ranks)

    def test_ties_broken_in_original_order(self, monkeypatch, data):
        # Equal confidence: the earliest row in the table is dropped first.
        claims = [
            {"claim": f"first low {'x ' * 20}", "source_id": 1, "quote": "", "confidence": "low"},
            {"claim": f"second low {'y ' * 20}", "source_id": 1, "quote": "", "confidence": "low"},
        ]
        proj_both = draft_agent._projected_input_tokens(claims, data, False, 2)
        proj_one = draft_agent._projected_input_tokens(claims[:1], data, False, 2)
        budget = (proj_one + proj_both) // 2

        _, info = enforce_token_budget(list(claims), data, False, num_sections=2, budget=budget)

        assert len(info["dropped_rows"]) == 1
        assert info["dropped_rows"] == [claims[0]]
        assert info["projected_tokens"] <= budget

    def test_extreme_budget_trims_everything(self, monkeypatch, data):
        trimmed, info = enforce_token_budget(
            _rows([("high", 2)]), data, False, num_sections=6, budget=1
        )
        assert trimmed == []
        assert info["projected_tokens"] > 0  # sources list still rendered

    def test_empty_evidence_is_safe(self, data):
        trimmed, info = enforce_token_budget([], data, False, num_sections=2, budget=10)
        assert trimmed == []
        assert info["dropped"] == 0

    def test_draft_answer_consults_budget_guard(self, monkeypatch):
        """draft_answer must route its evidence through the budget guard."""
        calls = {}

        class FakeLLM:
            def invoke(self, messages):
                class Resp:
                    content = "Section body. [1]"

                return Resp()

        monkeypatch.setattr(draft_agent, "_get_llm", lambda: FakeLLM())
        monkeypatch.setattr(draft_agent.time, "sleep", lambda s: None)
        original = draft_agent.enforce_token_budget

        def spy(evidence, data, deep_research, num_sections, budget=None):
            calls["args"] = (deep_research, num_sections)
            return original(evidence, data, deep_research, num_sections, budget)

        monkeypatch.setattr(draft_agent, "enforce_token_budget", spy)
        draft_agent.draft_answer(
            data=[{"content": "some finding", "url": "https://example.com"}],
            deep_research=True,
            retries=1,
        )
        assert calls["args"] == (True, 6)
