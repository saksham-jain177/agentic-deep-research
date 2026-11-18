"""Metric aggregation for the evaluation harness.

Definitions, stated exactly as implemented - the previous version defined
``citation_accuracy`` over "evaluable" pairs and silently dropped every pair
the judge could not score or the fetcher could not resolve, which meant an
agent was graded only on its successes (see eval/README.md, F3/F4):

* ``citation_accuracy``   - supported pairs / ALL mapped (claim, citation) pairs.
  An unresolved link, an off-topic source, an unsupported claim, and a judge
  that failed to answer all count against the score.
* ``link_validity``       - reachable pairs / all pairs. ``None`` unless the run
  actually probed URLs (``link_check_mode="http"``); with captured content the
  equivalent number is published as ``content_available_rate`` instead, because
  the two are different measurements.
* ``relevance_rate``      - on-topic pairs / pairs whose content was captured
* ``support_rate``        - supported pairs / on-topic pairs (conditional by
  design; read it next to citation_accuracy, never alone)
* ``judge_failure_rate``  - pairs the judge could not answer / capturable pairs

Behavioural and cost metrics sit alongside them: claim recall, abstention on
unanswerable queries, false abstention, truncation, latency, and $/report.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence

from .schema import QueryResult, TIERS

ABSTENTION_PATTERNS = (
    r"\bcould not (?:find|locate|identify|verify)\b",
    r"\b(?:unable|not able) to (?:find|verify|confirm|locate)\b",
    r"\bno (?:reliable|credible|public|verifiable) (?:sources?|evidence|data|information)\b",
    r"\binsufficient (?:evidence|data|information|sources)\b",
    r"\b(?:evidence|data) (?:is|are) (?:inconclusive|unavailable|not available)\b",
    r"\bdoes not (?:appear to )?exist\b",
    r"\bcannot be (?:determined|verified|confirmed)\b",
)


def detect_abstention(report: str, max_citations: int = 2) -> bool:
    """Heuristic: does the report say it could not answer?

    Requires both abstention language and a thin citation set, so a confident
    report that merely mentions "insufficient evidence" in passing is not
    miscounted as abstaining.
    """
    text = (report or "").lower()
    if not text.strip():
        return False
    hits = sum(1 for pattern in ABSTENTION_PATTERNS if re.search(pattern, text))
    if hits == 0:
        return False
    citations = len(set(re.findall(r"\[(\d+)\]", text)))
    return citations <= max_citations


def percent(numerator: float, denominator: float) -> Optional[float]:
    return round(100.0 * numerator / denominator, 1) if denominator else None


def _pairs(results: Sequence[QueryResult]):
    for result in results:
        for pair in result.pairs:
            yield result, pair


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (no numpy dependency)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[rank]


def compute_metrics(
    results: Sequence[QueryResult],
    link_check_mode: str = "captured_content",
) -> Dict[str, object]:
    """Aggregate every metric over a set of query results.

    ``link_check_mode`` records how tier 1 was measured: ``"http"`` when URLs
    were actually probed, ``"captured_content"`` when the run only confirmed
    that the research step captured text for the citation.
    """
    pairs = list(_pairs(results))
    n_pairs = len(pairs)
    link_ok = sum(1 for _, p in pairs if p.link_ok)
    with_content = [p for _, p in pairs if p.link_ok]
    unknown = [p for p in with_content if p.relevant is None]
    relevant = [p for p in with_content if p.relevant]
    supported = [p for _, p in pairs if p.is_supported]

    answered = [r for r in results if not r.error]
    scored_claims = [r for r in answered if r.expected_claims > 0]
    expected_claims = sum(r.expected_claims for r in scored_claims)
    covered_claims = sum(r.covered_claims for r in scored_claims)

    t4 = [r for r in answered if r.tier == "T4"]
    answerable = [r for r in answered if r.tier in ("T1", "T2")]
    total_claims = sum(len(r.claims) for r in answered)
    uncited_claims = sum(1 for r in answered for c in r.claims if not c.citations)

    latencies = [r.latency_s for r in answered if r.latency_s]
    costs = [r.cost_usd for r in answered]

    return {
        "n_queries": len(results),
        "n_answered": len(answered),
        "n_errors": len(results) - len(answered),
        "n_pairs": n_pairs,
        "n_claims": total_claims,
        "citation_accuracy": percent(len(supported), n_pairs),
        "link_check_mode": link_check_mode,
        "link_validity": percent(link_ok, n_pairs) if link_check_mode == "http" else None,
        "content_available_rate": percent(link_ok, n_pairs),
        "relevance_rate": percent(len(relevant), len(with_content)),
        "support_rate": percent(len(supported), len(relevant)),
        "judge_failure_rate": percent(len(unknown), len(with_content)),
        "effective_citations_per_report": round(len(supported) / len(answered), 2) if answered else 0.0,
        "claim_recall": percent(covered_claims, expected_claims),
        "uncited_claim_rate": percent(uncited_claims, total_claims),
        "abstention_rate_t4": percent(sum(1 for r in t4 if r.abstained), len(t4)),
        "false_abstention_rate": percent(sum(1 for r in answerable if r.abstained), len(answerable)),
        "truncated_queries": sum(1 for r in answered if r.truncated),
        "latency_p50_s": round(percentile(latencies, 50), 2),
        "latency_p95_s": round(percentile(latencies, 95), 2),
        "mean_cost_usd": round(sum(costs) / len(costs), 5) if costs else 0.0,
        "total_cost_usd": round(sum(costs), 4),
    }


def metrics_by_tier(
    results: Sequence[QueryResult],
    link_check_mode: str = "captured_content",
) -> Dict[str, Dict[str, object]]:
    return {
        tier: compute_metrics(
            [r for r in results if r.tier == tier], link_check_mode=link_check_mode
        )
        for tier in TIERS
        if any(r.tier == tier for r in results)
    }


def cohen_kappa(human: Sequence[bool], model: Sequence[bool]) -> Optional[float]:
    """Cohen's kappa between two binary label sequences.

    Reported alongside judge metrics: an uncalibrated judge is a number
    generator, not an evaluator.
    """
    if len(human) != len(model) or not human:
        return None
    n = len(human)
    agree = sum(1 for a, b in zip(human, model) if a == b)
    po = agree / n
    p_human_true = sum(1 for a in human if a) / n
    p_model_true = sum(1 for b in model if b) / n
    pe = p_human_true * p_model_true + (1 - p_human_true) * (1 - p_model_true)
    if pe >= 1.0:
        return 1.0 if po == 1.0 else 0.0
    return round((po - pe) / (1 - pe), 3)


def load_calibration(path: str) -> Dict[str, Optional[float]]:
    """Compute judge/human agreement from a JSONL of labelled pairs.

    Each line: ``{"human": true|false, "model": true|false, "kind": "support"}``
    """
    import json

    human: List[bool] = []
    model: List[bool] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            human.append(bool(row["human"]))
            model.append(bool(row["model"]))

    agree = sum(1 for a, b in zip(human, model) if a == b)
    return {
        "n": len(human),
        "raw_agreement": percent(agree, len(human)),
        "cohen_kappa": cohen_kappa(human, model),
    }
