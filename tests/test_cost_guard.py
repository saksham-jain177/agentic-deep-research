"""Tests for the pre-run cost gate (cost_guard.py, ROADMAP item 10).

The gate turns the cost estimator from a report into a control: when
``MAX_RUN_COST_USD`` is set, a run whose worst-case estimate exceeds the cap
is refused BEFORE any API call. The properties that matter:

* Unset (the default) changes nothing - every entry point behaves as before.
* A malformed cap fails closed (``ValueError``), never degrades to "no cap";
  a typo in a budget must stop the run, not uncap it.
* The boundary is strict: worst == cap is allowed, worst > cap is refused.
* The estimate the gate enforces is exactly the one the estimator (and thus
  the Streamlit UI) shows - the number a user approved is the number gated.
* All three entry points gate the live path and only the live path:
  ``main.run_research`` (web app), ``mcp_server.deep_research`` (agent
  interface), ``stream_cli.main`` (CLI). ``--mock`` is never gated - it
  spends nothing, and the offline demo must keep working under any cap.

Cap values in these tests are derived from the estimator's own output, not
hardcoded, so a pricing-table change cannot silently break the boundary
tests.
"""

import io
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cost_estimator  # noqa: E402
import cost_guard  # noqa: E402
import mcp_server  # noqa: E402
import stream_cli  # noqa: E402

QUERY = "climate change and agriculture"


def _worst(deep: bool) -> float:
    estimate = cost_estimator.estimate_research_cost(
        QUERY, deep_research=deep, target_word_count=1000
    )
    return float(estimate["cost_usd"]["max"])


def _cap_just_below(worst: float) -> str:
    # repr round-trips the float exactly; formatting to N decimals could round
    # the nextafter value back up to worst itself and silently test the
    # boundary instead of just-below it.
    return repr(math.nextafter(worst, 0.0))


class TestGetCap:
    def test_unset_means_no_cap(self, monkeypatch):
        monkeypatch.delenv(cost_guard.ENV_VAR, raising=False)
        assert cost_guard.get_cap() is None

    def test_empty_and_whitespace_mean_no_cap(self, monkeypatch):
        for raw in ("", "   "):
            monkeypatch.setenv(cost_guard.ENV_VAR, raw)
            assert cost_guard.get_cap() is None

    def test_parses_a_number(self, monkeypatch):
        for raw, expected in (("0.01", 0.01), (" 2.5 ", 2.5), ("0", 0.0)):
            monkeypatch.setenv(cost_guard.ENV_VAR, raw)
            assert cost_guard.get_cap() == expected

    def test_malformed_fails_closed(self, monkeypatch):
        monkeypatch.setenv(cost_guard.ENV_VAR, "ten dollars")
        with pytest.raises(ValueError, match="MAX_RUN_COST_USD"):
            cost_guard.get_cap()

    def test_negative_fails_closed(self, monkeypatch):
        monkeypatch.setenv(cost_guard.ENV_VAR, "-1")
        with pytest.raises(ValueError, match="negative"):
            cost_guard.get_cap()


class TestCheckRunCost:
    def test_no_cap_returns_the_estimators_own_numbers(self, monkeypatch):
        monkeypatch.delenv(cost_guard.ENV_VAR, raising=False)
        decision = cost_guard.check_run_cost(QUERY, deep_research=True)
        assert decision.allowed is True
        assert decision.cap_usd is None
        assert decision.reason == "no cap configured"
        assert decision.estimate == cost_estimator.estimate_research_cost(
            QUERY, deep_research=True, target_word_count=1000
        )

    def test_within_cap_allowed_with_reason(self, monkeypatch):
        worst = _worst(deep=True)
        monkeypatch.setenv(cost_guard.ENV_VAR, repr(worst + 1.0))
        decision = cost_guard.check_run_cost(QUERY, deep_research=True)
        assert decision.allowed is True
        assert decision.cap_usd == pytest.approx(worst + 1.0)
        assert "within cap" in decision.reason

    def test_over_cap_raises_with_details(self, monkeypatch):
        worst = _worst(deep=True)
        monkeypatch.setenv(cost_guard.ENV_VAR, _cap_just_below(worst))
        with pytest.raises(cost_guard.CostCapExceeded) as excinfo:
            cost_guard.check_run_cost(QUERY, deep_research=True)
        exc = excinfo.value
        assert exc.estimate["cost_usd"]["max"] == pytest.approx(worst)
        message = str(exc)
        assert "MAX_RUN_COST_USD" in message
        assert "before any API call" in message
        assert f"${worst:.4f}" in message  # the worst case it refused
        assert f"${exc.cap_usd:.4f}" in message  # the cap that refused it

    def test_boundary_worst_equals_cap_is_allowed(self, monkeypatch):
        worst = _worst(deep=False)
        monkeypatch.setenv(cost_guard.ENV_VAR, repr(worst))
        decision = cost_guard.check_run_cost(QUERY, deep_research=False)
        assert decision.allowed is True

    def test_zero_cap_refuses_any_positive_worst_case(self, monkeypatch):
        monkeypatch.setenv(cost_guard.ENV_VAR, "0")
        with pytest.raises(cost_guard.CostCapExceeded):
            cost_guard.check_run_cost(QUERY, deep_research=False)


class TestAssertWithinCap:
    def test_returns_the_estimate_when_within(self, monkeypatch):
        monkeypatch.delenv(cost_guard.ENV_VAR, raising=False)
        estimate = cost_guard.assert_within_cap(QUERY, deep_research=False)
        assert estimate["cost_usd"]["max"] == pytest.approx(_worst(False))

    def test_raises_when_over(self, monkeypatch):
        monkeypatch.setenv(cost_guard.ENV_VAR, "0.000001")
        with pytest.raises(cost_guard.CostCapExceeded):
            cost_guard.assert_within_cap(QUERY, deep_research=False)


class TestStreamCliGate:
    """The CLI is the entry point a script loop would hammer; it must gate."""

    def _run_main(self, argv, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["stream_cli.py"] + argv)
        return stream_cli.main()

    def test_over_cap_refuses_before_gathering(self, monkeypatch, capsys):
        monkeypatch.setenv(cost_guard.ENV_VAR, "0.000001")

        def _must_not_run(*args, **kwargs):
            raise AssertionError("research_web called despite the cost cap")

        monkeypatch.setattr("research_agent.research_web", _must_not_run)
        rc = self._run_main([QUERY], monkeypatch)
        assert rc == 2  # 2 = refused by the cap, distinct from 1 = gather failure
        err = capsys.readouterr().err
        assert "MAX_RUN_COST_USD" in err
        assert "refused" in err

    def test_uncapped_gather_failure_is_exit_1_not_2(self, monkeypatch, capsys):
        monkeypatch.delenv(cost_guard.ENV_VAR, raising=False)

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("research_agent.research_web", _boom)
        rc = self._run_main([QUERY], monkeypatch)
        assert rc == 1  # the gate never fired; this is the ordinary failure path
        assert "MAX_RUN_COST_USD" not in capsys.readouterr().err

    def test_mock_is_never_gated(self, monkeypatch, capsys):
        monkeypatch.setenv(cost_guard.ENV_VAR, "0.000001")
        rc = self._run_main([QUERY, "--mock"], monkeypatch)
        assert rc == 0


class TestWebAppGate:
    def test_run_research_refuses_over_cap_before_any_work(self, monkeypatch):
        import main

        monkeypatch.setenv(cost_guard.ENV_VAR, "0.000001")
        # If the gate failed to run first, any use of the compiled graph would
        # blow up on this sentinel - the CostCapExceeded below must win.
        monkeypatch.setattr(main, "app", object())
        with pytest.raises(cost_guard.CostCapExceeded, match="MAX_RUN_COST_USD"):
            main.run_research(QUERY, deep_research=True)


class TestMcpLiveGate:
    """The real guard through the frame protocol - not the mocked variant."""

    def _call_tool(self, arguments: dict) -> dict:
        stdin = io.StringIO(
            json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "deep_research", "arguments": arguments},
            }) + "\n"
        )
        stdout = io.StringIO()
        mcp_server.serve(stdin=stdin, stdout=stdout)
        frames = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
        return frames[0]["result"]

    def test_real_guard_refuses_over_the_frame_protocol(self, monkeypatch):
        monkeypatch.setenv(cost_guard.ENV_VAR, "0.000001")

        def _must_not_run(*args, **kwargs):
            raise AssertionError("research_web called despite the cost cap")

        monkeypatch.setattr("research_agent.research_web", _must_not_run)
        result = self._call_tool({"query": QUERY, "mock": False})
        assert result["isError"] is True
        assert "MAX_RUN_COST_USD" in result["content"][0]["text"]

    def test_mock_bypasses_the_guard_even_under_a_zero_cap(self, monkeypatch):
        monkeypatch.setenv(cost_guard.ENV_VAR, "0")
        result = self._call_tool({"query": QUERY, "mock": True})
        # MCP spec: isError defaults to false; a successful result may omit it.
        assert result.get("isError") is not True
