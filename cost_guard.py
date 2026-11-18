"""Pre-run cost control: ``MAX_RUN_COST_USD`` turns the estimator into a gate.

The cost estimator already reports what a run *should* cost (``cost_estimator``,
layered pricing with an explicit confidence level). Reporting is not control:
an agent-driven run - the MCP server, a loop in a script - can repeat a
spending call without a human watching the meter. This module adds the one
thing a report cannot do: refuse to start.

Behaviour:

* ``MAX_RUN_COST_USD`` unset (default) - no cap, every entry point behaves
  exactly as before. The guard is opt-in by design so adding it changes no
  existing workflow.
* Set and the estimate's worst case (``cost_max``) is at or under the cap -
  the run proceeds.
* Set and ``cost_max`` exceeds the cap - ``CostCapExceeded`` is raised before
  any API call is made. The exception carries the estimate so the caller (a
  UI, an agent) can show *why* it was refused.
* Set to something that is not a non-negative number - fail closed. A safety
  control that cannot be parsed must never degrade into "no cap"; a typo in a
  budget is exactly when you want the run to stop and say so.

The comparison uses ``cost_max``, not ``cost_avg``: a cap that the worst case
can blow through is not a cap. The estimate is exactly the one the Streamlit
UI already shows (``estimate_research_cost``), so the number a user approved
is the number the gate enforces.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from cost_estimator import estimate_research_cost

ENV_VAR = "MAX_RUN_COST_USD"


class CostCapExceeded(RuntimeError):
    """The pre-run estimate exceeds ``MAX_RUN_COST_USD``; nothing was spent."""

    def __init__(self, cap_usd: float, estimate: dict):
        self.cap_usd = cap_usd
        self.estimate = estimate
        worst = estimate.get("cost_usd", {}).get("max", 0.0)
        super().__init__(
            f"pre-run cost estimate ${worst:.4f} (worst case) exceeds "
            f"{ENV_VAR}=${cap_usd:.4f}; run refused before any API call"
        )


@dataclass
class CostDecision:
    """The outcome of a gate check, for callers that want details not exceptions."""

    allowed: bool
    cap_usd: Optional[float]
    estimate: Optional[dict]
    reason: str


def get_cap() -> Optional[float]:
    """Read the cap from the environment; ``None`` means no cap.

    Raises ``ValueError`` on a malformed value - fail closed, never silently
    unbounded.
    """
    raw = os.getenv(ENV_VAR, "").strip()
    if not raw:
        return None
    try:
        cap = float(raw)
    except ValueError:
        raise ValueError(
            f"{ENV_VAR}={raw!r} is not a number; fix or unset it - "
            "a malformed cost cap fails closed rather than running uncapped"
        )
    if cap < 0:
        raise ValueError(
            f"{ENV_VAR}={raw!r} is negative; a budget cannot be negative - "
            "fix or unset it"
        )
    return cap


def check_run_cost(
    query: str,
    deep_research: bool = False,
    target_word_count: int = 1000,
    model: str = "gpt-3.5-turbo",
) -> CostDecision:
    """Estimate the run's cost and refuse it when the cap would be exceeded.

    Raises ``CostCapExceeded`` when refused; returns the decision otherwise.
    With no cap configured this is a pure estimate (the same numbers the UI
    shows) and always allows.
    """
    estimate = estimate_research_cost(
        query, deep_research=deep_research, target_word_count=target_word_count, model=model
    )
    cap = get_cap()
    if cap is None:
        return CostDecision(
            allowed=True, cap_usd=None, estimate=estimate, reason="no cap configured"
        )
    worst = float(estimate.get("cost_usd", {}).get("max", 0.0))
    if worst > cap:
        raise CostCapExceeded(cap, estimate)
    return CostDecision(
        allowed=True,
        cap_usd=cap,
        estimate=estimate,
        reason=f"worst case ${worst:.4f} within cap ${cap:.4f}",
    )


def assert_within_cap(
    query: str,
    deep_research: bool = False,
    target_word_count: int = 1000,
    model: str = "gpt-3.5-turbo",
) -> dict:
    """Gate form for pipeline entry points: returns the estimate or raises."""
    decision = check_run_cost(
        query,
        deep_research=deep_research,
        target_word_count=target_word_count,
        model=model,
    )
    assert decision.estimate is not None
    return decision.estimate
