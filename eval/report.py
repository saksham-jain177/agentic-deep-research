"""Render run payloads as Markdown: the artifact that goes in the README."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

HEADLINE_KEYS = (
    ("citation_accuracy", "Citation accuracy (all pairs)"),
    ("content_available_rate", "Content captured for citation"),
    ("link_validity", "Link validity (URL probed)"),
    ("relevance_rate", "Relevance rate (of capturable)"),
    ("support_rate", "Support rate (of on-topic)"),
    ("judge_failure_rate", "Judge failure rate"),
    ("effective_citations_per_report", "Effective citations / report"),
    ("claim_recall", "Gold claim recall"),
    ("uncited_claim_rate", "Uncited claim rate"),
    ("abstention_rate_t4", "Abstention on unanswerable (T4)"),
    ("false_abstention_rate", "False abstention (T1-T2)"),
    ("truncated_queries", "Queries truncated at pair cap"),
    ("latency_p50_s", "Latency p50 (s)"),
    ("latency_p95_s", "Latency p95 (s)"),
    ("mean_cost_usd", "Mean cost / report (USD)"),
)

PERCENT_KEYS = {
    "citation_accuracy",
    "content_available_rate",
    "link_validity",
    "support_rate",
    "relevance_rate",
    "judge_failure_rate",
    "claim_recall",
    "uncited_claim_rate",
    "abstention_rate_t4",
    "false_abstention_rate",
}


def _fmt(value: object, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:g}{suffix}"
    return f"{value}{suffix}"


def format_metrics_table(metrics: Dict[str, object]) -> str:
    lines = ["| Metric | Value |", "| --- | --- |"]
    for key, label in HEADLINE_KEYS:
        suffix = "%" if key in PERCENT_KEYS else ""
        lines.append(f"| {label} | {_fmt(metrics.get(key), suffix)} |")
    lines.append(f"| Queries evaluated | {metrics.get('n_queries', 0)} |")
    lines.append(f"| Errors | {metrics.get('n_errors', 0)} |")
    lines.append(f"| Scored pairs | {metrics.get('n_pairs', 0)} |")
    return "\n".join(lines)


def format_tier_table(by_tier: Dict[str, Dict[str, object]]) -> str:
    lines = [
        "| Tier | Queries | Citation acc. | Support | Claim recall | Abstention | p95 latency |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for tier in sorted(by_tier):
        m = by_tier[tier]
        abstention = m.get("abstention_rate_t4") if tier == "T4" else m.get("false_abstention_rate")
        lines.append(
            "| {tier} | {n} | {acc} | {sup} | {rec} | {abst} | {p95} |".format(
                tier=tier,
                n=m.get("n_queries", 0),
                acc=_fmt(m.get("citation_accuracy"), "%"),
                sup=_fmt(m.get("support_rate"), "%"),
                rec=_fmt(m.get("claim_recall"), "%"),
                abst=_fmt(abstention, "%"),
                p95=_fmt(m.get("latency_p95_s"), "s"),
            )
        )
    return "\n".join(lines)


def failure_summary(payload: Dict[str, object], limit: int = 8) -> str:
    """Group pair-level failures by their cause, worst offenders last."""
    buckets: Dict[str, int] = {}
    for result in payload.get("results", []):
        for pair in result.get("pairs", []):
            notes = str(pair.get("notes", ""))
            if pair.get("link_ok") is False:
                if "dangling" in notes:
                    key = "citation number missing from reference list"
                elif "no URL" in notes:
                    key = "reference has no URL"
                else:
                    key = "citation did not resolve"
                buckets[key] = buckets.get(key, 0) + 1
            elif pair.get("relevant") is False:
                buckets["source off-topic for claim"] = buckets.get("source off-topic for claim", 0) + 1
            elif pair.get("supported") is False:
                buckets["source does not establish claim"] = buckets.get("source does not establish claim", 0) + 1
    if not buckets:
        return "_No failing pairs in this run._"
    lines = ["| Failure mode | Pairs |", "| --- | --- |"]
    for name, count in sorted(buckets.items(), key=lambda kv: -kv[1])[:limit]:
        lines.append(f"| {name} | {count} |")
    return "\n".join(lines)


def truncation_notice(metrics: Dict[str, object]) -> str:
    """Loud notice when a run hit the per-query pair cap (F11)."""
    truncated = metrics.get("truncated_queries") or 0
    if not truncated:
        return ""
    return (
        f"> **WARNING: {truncated} query(ies) hit the per-query pair cap and were scored on a "
        f"prefix.** Metrics here understate the work; raise the cap or split the report.\n\n"
    )


def render_markdown(payload: Dict[str, object], title: str = "Deep Research Agent - Evaluation") -> str:
    metadata = payload.get("metadata", {})
    metrics = payload.get("metrics", {})
    by_tier = payload.get("by_tier", {})

    parts: List[str] = [
        f"# {title}",
        "",
        f"_{metadata.get('n_queries', metrics.get('n_queries', 0))} queries | "
        f"judge: `{metadata.get('judge_name', 'unknown')}` | "
        f"mode: `{metadata.get('mode', 'unknown')}` | "
        f"commit: `{metadata.get('git_commit', 'n/a')}` | "
        f"run at {metadata.get('started_at', 'unknown')}_",
        "",
        "## Headline",
        "",
        format_metrics_table(metrics),
        "",
        truncation_notice(metrics),
        "## By tier",
        "",
        format_tier_table(by_tier),
        "",
        "## Where it fails",
        "",
        failure_summary(payload),
        "",
        "## Reading this table",
        "",
        "- **Citation accuracy** is the headline and its denominator is every mapped "
        "(claim, citation) pair. An unreachable link, an off-topic page, an unsupported "
        "claim and a judge that failed all count against it - the score cannot be "
        "raised by deleting citations or by a flaky judge.",
        "- **Content captured** and **link validity** are different measurements. The "
        "first says the research step stored text for that citation; the second says a "
        "URL was probed and answered. Only one of them is shown at a time.",
        "- **Support rate** is conditional on the source being on-topic; read it beside "
        "citation accuracy, never alone.",
        "- **Abstention on T4** and **false abstention on T1-T2** must be read together; "
        "either alone is trivially gameable.",
        "- **Queries truncated at pair cap** must be zero. A non-zero value means the "
        "run scored a prefix of some reports and the metrics understate the work.",
        "",
    ]
    if metadata.get("notes"):
        parts.extend(["> " + str(metadata["notes"]), ""])
    return "\n".join(parts)


def compare_runs(baseline: Dict[str, object], candidate: Dict[str, object]) -> str:
    """Delta table between two runs - the ablation view."""
    base = baseline.get("metrics", {})
    cand = candidate.get("metrics", {})
    lines = [
        "| Metric | Baseline | Candidate | Delta |",
        "| --- | --- | --- | --- |",
    ]
    for key, label in HEADLINE_KEYS:
        b, c = base.get(key), cand.get(key)
        if isinstance(b, (int, float)) and isinstance(c, (int, float)):
            delta = round(c - b, 3)
            sign = "+" if delta > 0 else ""
            lines.append(f"| {label} | {_fmt(b)} | {_fmt(c)} | {sign}{delta:g} |")
        else:
            lines.append(f"| {label} | {_fmt(b)} | {_fmt(c)} | n/a |")
    return "\n".join(lines)
