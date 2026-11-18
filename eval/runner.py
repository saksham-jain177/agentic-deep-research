"""Run the gold set through a pipeline and collect query results.

Two modes:

``replay``
    Read a saved ``PipelineOutput`` per query from ``--replay-dir``. Deterministic,
    free, and the only mode that belongs in CI. Record one with ``--record``
    from a live run, then commit it as a fixture.

``live``
    Import the agent's own ``main.run_research`` and run it for real. Requires
    API keys; every artifact it produces is timestamped and never silently
    overwrites a committed baseline.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

from .citations import parse_report
from .judge import Judge
from .metrics import compute_metrics, detect_abstention, metrics_by_tier
from .schema import GoldQuery, PipelineOutput, QueryResult
from .verify import LinkChecker, count_covered_claims, verify_pairs

Pipeline = Callable[[GoldQuery], PipelineOutput]


@dataclass
class RunMetadata:
    mode: str
    judge_name: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    git_commit: str = ""
    notes: str = ""
    # "http" when every cited URL was actually probed, "captured_content" when
    # tier 1 only confirms the research step captured text for that citation.
    # These are different measurements and must not share a column name.
    link_check_mode: str = "captured_content"


def _git_commit() -> str:
    try:
        import subprocess

        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ).decode().strip()
    except Exception:
        return ""


def replay_pipeline(replay_dir: str) -> Pipeline:
    """Load one ``<query-id>.json`` recording per query."""

    def run(gold: GoldQuery) -> PipelineOutput:
        path = os.path.join(replay_dir, f"{gold.id}.json")
        if not os.path.exists(path):
            return PipelineOutput(report="", error=f"no replay recording at {path}")
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return PipelineOutput(
            report=str(payload.get("report", "")),
            sources=list(payload.get("sources", [])),
            latency_s=float(payload.get("latency_s", 0.0)),
            input_tokens=int(payload.get("input_tokens", 0)),
            output_tokens=int(payload.get("output_tokens", 0)),
            cost_usd=float(payload.get("cost_usd", 0.0)),
            error=str(payload.get("error", "")),
        )

    return run


def _run_graph(query: str, deep_research: bool, word_count: int):
    """Preferred backend: the agent's own LangGraph workflow."""
    from main import run_research

    return run_research(
        query,
        deep_research=deep_research,
        target_word_count=word_count,
    )


def _run_direct(query: str, deep_research: bool, word_count: int):
    """Fallback backend: the same two nodes, without the graph wrapper.

    ``main`` is a two-node StateGraph (research -> draft), so calling the
    nodes directly is behaviourally equivalent. This path exists because
    ``requirements.txt`` does not currently resolve to a working langgraph
    install, and an evaluation harness that cannot run is worthless.
    """
    from draft_agent import draft_tool
    from research_agent import research_web

    research_data = research_web(query, deep_research, "en")
    if (
        isinstance(research_data, list)
        and research_data
        and isinstance(research_data[0], dict)
        and "error" in research_data[0]
    ):
        raise RuntimeError(f"research failed: {research_data[0]['error']}")

    draft = draft_tool.invoke(
        {
            "data": research_data,
            "deep_research": deep_research,
            "target_word_count": word_count,
            "writing_style": "academic",
            "citation_format": "APA",
            "language": "english",
            "retries": 3,
            "delay": 5,
        }
    )
    return research_data, draft


def live_pipeline(
    deep_research: bool = True,
    word_count: int = 1200,
    prefer_graph: bool = True,
) -> Pipeline:
    """Run the real agent. Imported lazily so replay mode needs no deps."""

    def run(gold: GoldQuery) -> PipelineOutput:
        backend = _run_graph if prefer_graph else _run_direct
        started = time.perf_counter()
        try:
            try:
                research_data, draft = backend(gold.query, deep_research, word_count)
            except ImportError:
                backend = _run_direct
                research_data, draft = backend(gold.query, deep_research, word_count)
        except Exception as exc:  # pragma: no cover - network path
            return PipelineOutput(report="", error=f"{type(exc).__name__}: {exc}")
        elapsed = time.perf_counter() - started

        error = ""
        if not draft or str(draft).startswith("Workflow failed") or "Error drafting response" in str(draft):
            error = str(draft)[:500]
        return PipelineOutput(
            report="" if error else str(draft),
            sources=list(research_data or []),
            latency_s=elapsed,
            error=error,
        )

    return run


def evaluate(
    queries: Sequence[GoldQuery],
    pipeline: Pipeline,
    judge: Judge,
    link_checker: Optional[LinkChecker] = None,
    tiers: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    record_dir: Optional[str] = None,
    ids: Optional[Sequence[str]] = None,
) -> List[QueryResult]:
    """Evaluate every gold query; one failure never aborts the run."""
    selected = [q for q in queries if not tiers or q.tier in tiers]
    if ids:
        wanted = set(ids)
        selected = [q for q in selected if q.id in wanted]
    if limit:
        selected = selected[:limit]

    results: List[QueryResult] = []
    for gold in selected:
        output = pipeline(gold)
        if record_dir:
            os.makedirs(record_dir, exist_ok=True)
            with open(os.path.join(record_dir, f"{gold.id}.json"), "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "query": gold.query,
                        "tier": gold.tier,
                        "report": output.report,
                        "sources": output.sources,
                        "latency_s": output.latency_s,
                        "input_tokens": output.input_tokens,
                        "output_tokens": output.output_tokens,
                        "cost_usd": output.cost_usd,
                        "error": output.error,
                    },
                    handle,
                    indent=2,
                )

        result = QueryResult(
            query_id=gold.id,
            tier=gold.tier,
            query=gold.query,
            expected_claims=len(gold.expected_claims),
            latency_s=output.latency_s,
            cost_usd=output.cost_usd,
            input_tokens=output.input_tokens,
            output_tokens=output.output_tokens,
            n_sources=len(output.sources),
            error=output.error,
        )

        if not output.error and output.report.strip():
            parsed = parse_report(output.report)
            result.claims = parsed.claims
            result.pairs = verify_pairs(parsed, output.sources, judge, link_checker)
            # Detect truncation rather than trusting the cap silently: the
            # report tells us how many pairs there were.
            result.pairs_expected = sum(len(c.citations) for c in parsed.claims)
            result.truncated = len(result.pairs) < result.pairs_expected
            if gold.expected_claims:
                result.covered_claims = count_covered_claims(parsed, gold)
            result.abstained = detect_abstention(parsed.body)

        results.append(result)

    return results


def build_payload(results: Sequence[QueryResult], metadata: RunMetadata) -> Dict[str, object]:
    return {
        "metadata": {
            **asdict(metadata),
            "git_commit": metadata.git_commit or _git_commit(),
        },
        "metrics": compute_metrics(results, link_check_mode=metadata.link_check_mode),
        "by_tier": metrics_by_tier(results, link_check_mode=metadata.link_check_mode),
        "results": [r.to_dict() for r in results],
    }


def save_payload(payload: Dict[str, object], out_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return out_path
