# Evaluation harness

The agent already ships a verification pass: an LLM judge scores each section
against its sources and the report says `supported` / `partially_supported` /
`unsupported`. That answers *"did this report pass our check?"*

This package answers a different question: **"how good is the agent, and is it
getting better?"** It is a benchmark, not a feature. It runs a fixed gold set
through the pipeline and reports citation accuracy, grounding, abstention,
and cost — reproducibly, offline, with no API keys required.

```
eval/
├── goldset/queries.json   # 40 stratified queries with expected claims
├── replays/               # committed pipeline recordings (deterministic runs)
├── citations.py           # report -> references + sentence-level claims
├── verify.py              # three-tier (claim, source) verification
├── judge.py               # heuristic + LLM judges
├── metrics.py             # aggregation, kappa, abstention detection
├── runner.py              # replay / live execution
├── report.py              # Markdown tables, run comparison
└── __main__.py            # CLI
```

Everything here is standard library only. It does not import the agent, the
LLM stack, or ChromaDB, so it runs in any CI job regardless of what else is
installed, and the harness cannot be accidentally "fixed" by changing the
thing it measures.

## Quick start

```bash
python -m eval validate                    # check the gold set structure
python -m eval run --mode replay           # deterministic run over committed fixtures
python -m eval run --mode replay --ids T1-01,T4-01 --out results/smoke.json
python -m eval report --results results/smoke.json --out results/smoke.md
```

A full run over the shipped fixtures is a smoke test, not a benchmark — see
*Recording fixtures* below to cover all 40 queries.

## What is measured

Every `[n]` citation in the report becomes one **(claim, source) pair**, and
each pair passes through three cumulative tiers:

| Tier | Question | Failure means |
| --- | --- | --- |
| `link_ok` | Was text captured for this citation, or did the URL answer if probed? | dead link, paywall, missing capture |
| `relevant` | Is that content about the claim's subject? | citation laundering — real link, wrong page |
| `supported` | Does the source actually establish the claim? | claim conflation, drift, hallucinated number |

| Metric | Definition |
| --- | --- |
| `citation_accuracy` | supported pairs / **all** mapped pairs — **the headline** |
| `content_available_rate` | pairs with captured content / all pairs |
| `link_validity` | reachable pairs / all pairs — `n/a` unless `--check-links` ran |
| `relevance_rate` | on-topic pairs / capturable pairs |
| `support_rate` | supported pairs / on-topic pairs (conditional by design) |
| `judge_failure_rate` | pairs the judge could not answer / capturable pairs |
| `effective_citations_per_report` | supported pairs per report (volume guard) |
| `claim_recall` | gold claims found / gold claims expected |
| `uncited_claim_rate` | claims with no `[n]` / all claims |
| `abstention_rate_t4` | unanswerable queries correctly refused |
| `false_abstention_rate` | answerable queries wrongly refused |
| `truncated_queries` | queries that hit the per-query pair cap — must be 0 |
| `latency_p50/p95`, `mean_cost_usd` | cost of the run |

Two properties matter more than the exact numbers:

- **Every failure counts against `citation_accuracy`.** An unresolved link, an
  off-topic page, an unsupported claim and a judge that failed all sit in the
  denominator. You cannot raise the score by deleting citations or by having a
  flaky judge.
- **`content_available_rate` and `link_validity` are never conflated.** They
  answer different questions; only one is populated per run and the run
  metadata records which (`link_check_mode`).

`abstention_rate_t4` and `false_abstention_rate` are only meaningful together:
an agent that refuses everything scores 100% on the first and 100% on the
second.

## Gold set

40 queries in four tiers, because a single aggregate hides the interesting
failures:

| Tier | Count | Tests |
| --- | --- | --- |
| T1 | 12 | single verifiable fact — basic grounding |
| T2 | 12 | synthesis across several sources |
| T3 | 8 | genuinely contested topics — must surface disagreement, not resolve it silently |
| T4 | 8 | unanswerable — must abstain rather than invent |

T4 is the tier most projects skip. An agent that confidently fabricates an
answer to *"what will the S&P 500 close at on 31 December 2031?"* is worse
than one that says it cannot know, and this is the only tier that catches it.

Edit `goldset/queries.json` to add queries; `python -m eval validate` enforces
structure (T4 must set `must_abstain`, every other tier needs claims).

## Judges

`--judge heuristic` (default) is deterministic lexical overlap. It is a
**conservative lower bound**: it misses paraphrase, so its support rate
understates the agent. It exists so CI can run without keys.

`--judge llm` uses `EVAL_JUDGE_MODEL` (falling back to `VERIFICATION_MODEL`)
and requires `EVAL_JUDGE_API_KEY` or `OPENROUTER_API_KEY`. Verdicts are binary
and must cite a verbatim quote from the source, because free-form scores are
not reproducible.

A judge is only trustworthy once you have measured it against yourself:

```bash
# results/judge_labels.jsonl - one {"human": bool, "model": bool} per line
python -m eval calibrate --labels results/judge_labels.jsonl
```

Aim for Cohen's κ ≥ 0.7 before quoting any number the judge produced. The
command exits non-zero below 0.6 so it can gate a release.

## Recording fixtures

`--mode live` runs the real agent (requires API keys) and `--record-dir`
saves one JSON per query. Commit those to `eval/replays/` and every later
`--mode replay` run is free, instant, and deterministic:

```bash
python -m eval run --mode live --record-dir eval/replays --limit 5 --out results/live.json
```

Re-record deliberately, and keep the old fixtures in git history — that is
how you show a number moved because the agent changed, not because the test
did.

## Ablations

`compare` renders the delta table that belongs in a PR description:

```bash
python -m eval run --mode replay --out results/before.json
# ... change one thing: reranking on, cache off, retrieval depth capped ...
python -m eval run --mode replay --out results/after.json
python -m eval compare --baseline results/before.json --candidate results/after.json
```

One variable per run. A pipeline change that moves three metrics at once
tells you nothing.

## Defects found and fixed

A line-by-line audit of this harness found four defects, all of which biased
the score upward. All four are fixed, each with a regression test in
`tests/test_eval_harness.py::TestRegressionFixes`.

| ID | Defect | Fix |
| --- | --- | --- |
| F1 | Abstention was computed on the raw pipeline output, so the reference list's `[n]` markers vetoed every abstention in live mode | `ParsedReport.body` strips the bibliography; `detect_abstention` reads that |
| F2 | `link_validity` reported captured content as if it were URL reachability | Split into `content_available_rate` and `link_validity` (the latter `n/a` unless `--check-links`) |
| F3/F4 | The denominator was "pairs the judge could score", so dead links and judge failures raised the headline | `citation_accuracy` divides by all mapped pairs; `judge_failure_rate` is reported |
| F11 | The 400-pair cap silently truncated long reports | `pairs_expected` and `truncated` are computed and rendered as a warning |

Impact on the same recording (`results/recordings/T1-01.json`: 155 pairs, 1
supported):

| Metric | Pre-fix | Post-fix |
| --- | --- | --- |
| `citation_accuracy` | 14.3% (1/7) | 0.6% (1/155) |

On the four hand-authored fixtures the numbers do not move (72.7% both ways):
those fixtures contain no unresolved citations and no judge failures. That is
the intended behaviour — the fix changes nothing where nothing was broken, and
changes everything where the old denominator was hiding the damage.

## Agent-side defects still open

One live query (`T1-01`, deep research, 28 sources gathered) surfaced two
defects in the *agent's* output that the harness cannot fix for it:

1. **Every reference is labelled `[1]`.** The `references` array in the draft
   JSON contains distinct URLs, but each carries the same bracketed number.
   Anything that resolves citations by their printed number — a reader, a
   renderer, a downstream checker — is therefore pointing at the wrong source.
2. **The report cites `[1]`..`[17]` but ships 5 references.** Twelve distinct
   citation numbers have no corresponding entry, so those claims are
   unattributable by construction.

The harness normalises around (1) by renumbering references in array order,
which is why `results/live-T1-01.json` still yields a measurable result. It
does not paper over (2): 148 of 155 mapped pairs are flagged as dangling, and
that is the correct verdict. Until the reference list is complete, citation
accuracy cannot rise above the fraction of numbered citations that have an
entry — which is the point of measuring it.

**Next fix, in order:** make `references` cover every citation number used in
the body, and number them sequentially. Everything else in this harness is
downstream of that.

## Limitations


- **Heuristic judge understates support.** Treat heuristic runs as relative
  comparisons, never as headline claims.
- **Replay fixtures are point-in-time.** Web content and URLs decay; a
  fixture from six months ago may no longer reflect the live site.
- **The gold set is 40 queries.** That is enough to detect a regression, not
  enough to claim a capability. Report it as what it is.
- **Claim matching is lexical.** A correctly paraphrased claim that shares
  little vocabulary with the gold text will be counted as missing.
