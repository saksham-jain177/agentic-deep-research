#!/usr/bin/env python
"""Post the replay-eval delta on a pull request (ROADMAP P0 item 5).

Compares two ``python -m eval run --mode replay`` result files - one from `main`, one from the
PR head - and prints a comment: the metric delta table, whether the two runs are comparable at
all, and which rows moved against the project. Posting only happens when Actions supplies
`GITHUB_TOKEN`; the comparison itself is offline and keyless.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.report import HEADLINE_KEYS, compare_runs  # noqa: E402

MARKER = "<!-- agentic-deep-replay-eval -->"

LOWER_IS_BETTER = {
    "judge_failure_rate",
    "uncited_claim_rate",
    "false_abstention_rate",
    "latency_p50_s",
    "latency_p95_s",
    "mean_cost_usd",
    "truncated_queries",
}


def _metrics(run: Dict[str, object]) -> Dict[str, object]:
    return run.get("metrics", {})  # type: ignore[return-value]


def scored_queries(metrics: Dict[str, object]) -> int:
    return int(metrics.get("n_queries", 0)) - int(metrics.get("n_skipped", 0))


def find_changes(base: Dict[str, object], cand: Dict[str, object]) -> List[str]:
    """Metric labels that moved against the project.

    No tolerance: replay scores the committed recordings with the heuristic judge, so a
    non-zero delta is the code's doing, not measurement noise.
    """
    worse = []
    for key, label in HEADLINE_KEYS:
        b, c = base.get(key), cand.get(key)
        if not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
            continue
        delta = c - b
        direction = -1 if key in LOWER_IS_BETTER else 1
        if delta and delta * direction < 0:
            worse.append(label)
    return worse


def build_comment(base_run: Dict[str, object], cand_run: Dict[str, object]) -> Tuple[str, List[str]]:
    base, cand = _metrics(base_run), _metrics(cand_run)
    b_scored, c_scored = scored_queries(base), scored_queries(cand)
    meta_base, meta_cand = base_run.get("metadata", {}), cand_run.get("metadata", {})

    worse = find_changes(base, cand)
    if b_scored != c_scored or base.get("n_queries") != cand.get("n_queries"):
        comparability = (
            f"**Not comparable as an ablation.** `main` scored {b_scored} of {base.get('n_queries')} "
            f"gold queries and this branch scored {c_scored} of {cand.get('n_queries')}, so the table "
            "below mixes a code change with a change in what was measured. Either re-record the "
            "missing fixtures on both sides or read it as two separate reports."
        )
    else:
        comparability = (
            f"**Comparable.** Both sides scored {b_scored} recorded queries "
            f"({cand.get('coverage_pct')}% of the {cand.get('n_queries')}-query gold set), one "
            "variable changed: this branch's code."
        )

    lines = [
        MARKER,
        "## Replay eval vs `main`",
        "",
        comparability,
        "",
        compare_runs(base_run, cand_run),
        "",
        (
            "No headline metric moved against the project."
            if not worse
            else "**Worse than `main`:** " + "; ".join(worse)
        ),
        "",
        "Coverage is part of the number: 4 of 40 gold queries have replay recordings, so these "
        "are not 40-query results and must not be quoted as such (see `ROADMAP.md` P0 item 1). "
        "Latency and cost come from those recordings too, so a pipeline change cannot move them "
        "here - they only move when the fixtures are re-recorded. Read a zero delta in those rows "
        "as *not measured*, not as *not slower*.",
        "",
        f"Baseline `{meta_base.get('git_commit', '?')}` (main) at "
        f"{meta_base.get('started_at', '?')[:19]} | this run "
        f"`{meta_cand.get('git_commit', '?')}` at {meta_cand.get('started_at', '?')[:19]}, "
        f"judge `{meta_cand.get('judge_name', '?')}`, mode `{meta_cand.get('mode', '?')}`.",
        "",
        "Reproduce: `python -m eval run --mode replay --out results/pr.json` on each side.",
    ]
    return "\n".join(lines), worse


def post_comment(body: str, *, token: Optional[str] = None, repo: Optional[str] = None,
                 pr: Optional[str] = None, api_root: Optional[str] = None) -> str:
    """Create, or replace, the single bot comment on this PR. Returns a description."""
    token = token or os.environ.get("GITHUB_TOKEN", "")
    repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
    api_root = (api_root or os.environ.get("GITHUB_API_URL", "https://api.github.com")).rstrip("/")
    if pr is None:
        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        pr = str(json.load(open(event_path)).get("number", "")) if event_path else ""
    if not token or not repo or not pr:
        raise SystemExit("--post needs GITHUB_TOKEN, GITHUB_REPOSITORY and a PR number")

    def request(method: str, url: str, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read() or b"{}")

    comments = request("GET", f"{api_root}/repos/{repo}/issues/{pr}/comments?per_page=100")
    existing = next((c for c in comments if MARKER in c.get("body", "")), None)
    if existing:
        request("PATCH", f"{api_root}/repos/{repo}/issues/comments/{existing['id']}", {"body": body})
        return f"updated comment {existing['id']}"
    created = request("POST", f"{api_root}/repos/{repo}/issues/{pr}/comments", {"body": body})
    return f"posted comment {created.get('id')}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="results JSON from main")
    parser.add_argument("--candidate", required=True, help="results JSON from the PR head")
    parser.add_argument("--out", help="write the markdown here instead of stdout")
    parser.add_argument("--post", action="store_true", help="create/update the PR comment")
    args = parser.parse_args(argv)

    with open(args.baseline, encoding="utf-8") as handle:
        base_run = json.load(handle)
    with open(args.candidate, encoding="utf-8") as handle:
        cand_run = json.load(handle)

    body, worse = build_comment(base_run, cand_run)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(body + "\n")
        print(f"wrote {args.out}")
    else:
        print(body)
    if args.post:
        print(post_comment(body))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
