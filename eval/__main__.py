"""CLI for the evaluation harness.

    python -m eval validate
    python -m eval run --mode replay --out results/baseline.json
    python -m eval run --mode live --record-dir eval/replays --limit 5
    python -m eval report --results results/baseline.json --out results/baseline.md
    python -m eval compare --baseline results/baseline.json --candidate results/rerank.json
    python -m eval calibrate --labels results/judge_labels.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .goldset import load_goldset, tier_counts, validate_goldset
from .judge import HeuristicJudge, build_openrouter_judge
from .metrics import load_calibration
from .report import compare_runs, render_markdown
from .runner import RunMetadata, build_payload, evaluate, live_pipeline, replay_pipeline, save_payload
from .verify import default_link_checker

DEFAULT_GOLDSET = os.path.join(os.path.dirname(__file__), "goldset", "queries.json")
DEFAULT_REPLAY_DIR = os.path.join(os.path.dirname(__file__), "replays")


def _judge(args):
    if args.judge == "heuristic":
        return HeuristicJudge()
    judge = build_openrouter_judge()
    if judge is None:
        print(
            "warning: no judge API key found (set EVAL_JUDGE_API_KEY or OPENROUTER_API_KEY); "
            "falling back to the heuristic judge, which understates support.",
            file=sys.stderr,
        )
        return HeuristicJudge()
    return judge


def cmd_validate(args) -> int:
    queries = load_goldset(args.goldset)
    problems = validate_goldset(queries)
    counts = tier_counts(queries)
    print(f"{len(queries)} queries: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for problem in problems:
        print(f"  ! {problem}")
    return 1 if problems else 0


def cmd_run(args) -> int:
    queries = load_goldset(args.goldset)
    tiers = [t.strip().upper() for t in args.tiers.split(",")] if args.tiers else None

    pipeline = replay_pipeline(args.replay_dir) if args.mode == "replay" else live_pipeline()
    judge = _judge(args)
    link_checker = default_link_checker if args.check_links else None

    if args.mode == "replay" and not os.path.isdir(args.replay_dir):
        print(f"error: replay directory not found: {args.replay_dir}", file=sys.stderr)
        return 2

    results = evaluate(
        queries,
        pipeline,
        judge,
        link_checker=link_checker,
        tiers=tiers,
        limit=args.limit,
        record_dir=args.record_dir,
        ids=[i.strip() for i in args.ids.split(",")] if args.ids else None,
    )

    payload = build_payload(
        results,
        RunMetadata(
            mode=args.mode,
            judge_name=getattr(judge, "name", "unknown"),
            notes=args.notes or "",
            link_check_mode="http" if args.check_links else "captured_content",
        ),
    )
    save_payload(payload, args.out)
    print(render_markdown(payload))
    print(f"\nWrote {args.out}")
    return 0


def cmd_report(args) -> int:
    with open(args.results, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    markdown = render_markdown(payload, title=args.title)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
        print(f"Wrote {args.out}")
    else:
        print(markdown)
    return 0


def cmd_compare(args) -> int:
    with open(args.baseline, "r", encoding="utf-8") as handle:
        baseline = json.load(handle)
    with open(args.candidate, "r", encoding="utf-8") as handle:
        candidate = json.load(handle)
    table = compare_runs(baseline, candidate)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(table + "\n")
        print(f"Wrote {args.out}")
    else:
        print(table)
    return 0


def cmd_calibrate(args) -> int:
    report = load_calibration(args.labels)
    print(json.dumps(report, indent=2))
    kappa = report.get("cohen_kappa")
    if kappa is None:
        print("warning: no labels found", file=sys.stderr)
        return 1
    if kappa < 0.6:
        print("warning: kappa below 0.6 - the judge is not reliable enough to publish", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eval", description="Deep Research Agent evaluation harness")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="check the gold set for structural problems")
    validate.add_argument("--goldset", default=DEFAULT_GOLDSET)
    validate.set_defaults(func=cmd_validate)

    run = sub.add_parser("run", help="evaluate the agent against the gold set")
    run.add_argument("--mode", choices=("replay", "live"), default="replay")
    run.add_argument("--goldset", default=DEFAULT_GOLDSET)
    run.add_argument("--replay-dir", default=DEFAULT_REPLAY_DIR)
    run.add_argument("--record-dir", default=None, help="save pipeline outputs as replay fixtures")
    run.add_argument("--judge", choices=("heuristic", "llm"), default="heuristic")
    run.add_argument("--check-links", action="store_true", help="HTTP-check every cited URL (slow, networked)")
    run.add_argument("--tiers", default=None, help="comma-separated subset, e.g. T1,T4")
    run.add_argument("--ids", default=None, help="comma-separated query ids, e.g. T1-01,T4-03")
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--notes", default="")
    run.add_argument("--out", default=os.path.join("results", "latest.json"))
    run.set_defaults(func=cmd_run)

    report = sub.add_parser("report", help="render a saved run as Markdown")
    report.add_argument("--results", required=True)
    report.add_argument("--out", default=None)
    report.add_argument("--title", default="Deep Research Agent - Evaluation")
    report.set_defaults(func=cmd_report)

    compare = sub.add_parser("compare", help="delta table between two runs")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--out", default=None)
    compare.set_defaults(func=cmd_compare)

    calibrate = sub.add_parser("calibrate", help="judge/human agreement from a JSONL label file")
    calibrate.add_argument("--labels", required=True)
    calibrate.set_defaults(func=cmd_calibrate)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
