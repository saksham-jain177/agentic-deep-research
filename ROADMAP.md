# Roadmap / taskpad

Working list for `agentic-deep-research`, ordered by **(impact ÷ cost)** rather
than by fun. Everything here is scoped to a personal project that must cost
about nothing to run and to host.

Cost key: `$0` = free tier / local only. `~$` = may consume a few free-tier API
calls. Hours are focused hours, not calendar days.

Status legend: `[ ]` open · `[x]` done · `[~]` partial.

---

## Done

- `[x]` **Bibliography numbering matched the inline markers** — `draft_agent`
  printed `[1]` on every reference and let the body cite numbers the reference
  list did not contain. This was the single largest known hit on citation
  accuracy (see `eval/README.md`). Fixed with a per-run citation registry;
  covered by `tests/test_citation_numbering.py`.
- `[x]` **Test isolation** — `os.environ` is restored after every test, so
  `load_dotenv()` inside one test no longer decides another test's verdict.
- `[x]` **Weighted per-section word budgets** — a 1000-word deep report asked
  every section for 166 words; budgets now follow the section's role
  (`_section_word_budget`), so Key Findings gets 270 and the Abstract 80.
- `[x]` **Removed the lambda `Tool` wrapper** around `research_web`. Nothing
  used it since the workflow started calling `research_web` directly, and it
  relied on undocumented kwargs forwarding through `Tool.run`.
- `[x]` **Eval coverage is now explicit** — a gold query with no replay
  recording counts as *skipped*, not *errored*, and the table prints
  `Queries scored 4 (10% of gold set)`. A partial replay can no longer be
  misread as a 40-query benchmark (`results/replay-goldset.md`).
- `[x]` **README links match the renamed repo** — the clone command, `cd`, and
  the issues link said `AI-Agent-based-Deep-Research`; the discussions link 404s.
- `[x]` **Security writeup exists** (`SECURITY.md`, item 7) — threat model, the
  mitigations that are really in the code (`sanitize.py`, `log_redaction.py`, PDF
  escaping, budget guards, push protection), and an honest list of what they do
  *not* guarantee. Heuristic sanitisation and the judge's non-security role are
  stated as limitations rather than sold as protections.
- `[x]` **`CHANGELOG.md`** (first half of item 11) — the 20 merged PRs reconstructed
  into releases, with the offline-test count and eval coverage at each point. The tag
  is cut: [`v0.5.0`](https://github.com/saksham-jain177/agentic-deep-research/releases/tag/v0.5.0)
  (2026-11-18), annotated with the reproduction command and the 4-of-40 coverage.
- `[x]` **The README answers "why not GPT-Researcher?"** (item 23), above the
  problem statement.
- `[x]` **`--mock` was spending the caller's credits** — it patched only the draft
  LLM, so on a machine with a real key the *judge* and the *contradiction pass*
  still built live clients and billed the demo the README calls free. All three
  entry points are now stubbed, and the real key is cleared for the duration of
  the run and restored afterwards. `tests/test_cli_report.py` pins both halves.
- `[x]` **One-command report surface** (item 9, partial) — `python stream_cli.py
  "query" --deep --out report.md` writes the full Markdown report with
  per-section verdicts, and `--mock` shows the same protocol offline with no key
  at all. The hosted demo still sits behind a Community Cloud login wall, so the
  README now says so and points at the two commands that work; the GIF is open.
- `[x]` **The poisoned-page fixture** (item 7b) — `tests/fixtures/poisoned_wheat_page.html` plus
  `tests/test_poisoned_page.py` (11 tests) run a hostile page through fetch → evidence → draft →
  judge with a stub model that *obeys* the payload, so the assertion is about the pipeline, not about
  whether a model would have refused. It caught a fact the security writeup had not stated: an
  invented citation number (`[7]` against a three-source table) is dropped and never reaches the
  bibliography, but the same page scored **first** under the real cross-encoder — which makes its
  demand to be cited as `[1]` a citation of a source that genuinely carries the quote, and the
  drafting layer has nothing to object to. `SECURITY.md` now records that limitation instead of
  implying the sanitiser covers it. Suite: 336 offline tests.
- `[x]` **Replay eval on every PR** (P0 item 5) — `.github/workflows/eval-comment.yml` checks out
  `main` and the PR head, runs `python -m eval run --mode replay` in both, and
  `scripts/eval_ci_comment.py` posts or edits one comment with the delta table. No tolerance band,
  because replay is deterministic: fixed recordings, heuristic judge. The comment also refuses two
  misreadings — a branch that changes *which* queries are scored is labelled not comparable as an
  ablation, and the latency/cost rows are marked *not measured* rather than *not slower*, since
  those numbers come from the recordings and no pipeline edit can move them. Posting is gated on
  the Actions token, so nothing reaches the network from a dev machine. Suite: 346 offline tests.

---

## P0 — finish what is measurable, before adding anything

Do these first. Each one converts work already in the repo into a number you
can publish, which is the only thing that distinguishes this project from the
other deep-research clones.

| # | Task | Why | Cost | Hrs |
| --- | --- | --- | --- | --- |
| 1 | **Re-record the live eval fixtures** with the current build (`python -m eval run --mode live --record-dir eval/replays`) and commit the before/after table. | Coverage is 4 of 40 (`eval/replays/` holds T1-01, T1-12, T2-01, T4-01), so every headline number describes a tenth of the gold set. Until the fix is re-measured you cannot claim it. ~28 searches + a few hundred completions on free tiers. | ~$0 | 2 |
| 2 | ~~**Close eval findings F5 and F8.**~~ **done** — F5: `find_reference_text` now matches exact URLs after normalisation, resolves a domain only when exactly one gathered source carries it, and falls back to position only when the reference has no URL at all; ambiguity fails the pair instead of guessing. F8: `eval run --calibration` enforces the κ ≥ 0.6 gate (exit 1 unless `--allow-uncalibrated`), records the calibration in run metadata, and the rendered report marks an uncalibrated run **UNCALIBRATED**. 14 regression tests in `tests/test_eval_source_matching.py`; suite 360 green. | The harness's credibility is the project's credibility. | $0 | 4 |
| 3 | **Grow the gold set from 40 to ~120 queries**, keeping the T1–T4 stratification. | 40 is a regression detector, not a capability claim (the README says so honestly). At 120 with the same tiers you can publish a per-tier table that survives scrutiny. Source queries from HotpotQA/SimpleQA/FRAMES public samples — free, licensed, and already curated by others. | $0 | 6 |
| 4 | **Publish a benchmark page**: one Markdown file per release with the metric table, the exact command to reproduce it, the commit SHA, and the fixture set. | This is the artifact a reviewer actually reads. Nobody runs your code first; they read your numbers and your method. | $0 | 2 |
| 5 | ~~**Wire `eval compare` into CI as a comment**~~ **done** (post-tag, see Unreleased in `CHANGELOG.md`) — `.github/workflows/eval-comment.yml` replays the gold set on both `main` and the PR head, then `scripts/eval_ci_comment.py` posts or updates one comment per PR. | Makes "no regression" mechanical instead of a claim, and every PR becomes a documented experiment. Runs offline, ~2 min. | $0 | 3 |

## P0b — measured against someone else's ruler

Every number in this README was produced by your harness, on your queries, with
your judge. That is the weakest kind of evidence and reviewers know it. These
three items change the class of the claim. Items 16 and 18 are a weekend at ~$0;
item 17 is the only one in this plan that costs real money, and the row states
what it actually costs rather than hoping it is free.

| # | Task | Why | Cost | Hrs |
| --- | --- | --- | --- | --- |
| 16 | **Self-score a 10-task pilot with [DeepResearch Bench](https://deepresearch-bench.github.io/)** (100 PhD-level tasks, 22 fields; RACE for report quality, FACT for citation support) using its public harness: your agent generates, their pipeline judges. | It is the one public benchmark built for exactly this genre, and FACT (extract statement-URL pairs, then check whether the cited page supports the claim) is a close relative of your three-tier citation scoring, so the comparison is apples-ish rather than decorative. Scoring yourself with *their* harness and publishing the config is still reproducible by a stranger. | $0–2 (FACT also wants a Jina scraping key) | 6 |
| 17 | **Official leaderboard entry**, if the pilot is respectable. Verified mechanics: email the two listed USTC maintainers with (a) a **temporary key with GPT-5.5 access** for their evaluation, (b) raw reports in their `raw_data/*.jsonl` shape, (c) a reproducibility link, (d) model name + licence. Cap the key and expect them to spend against it. | Being listed beside named systems is a credential; a star count is a popularity contest. This is the one item in the plan that is genuinely not $0: the official path pays for *their* judge on *your* key, and the number is only comparable if the model metadata and config are disclosed. | ~$20–60 of GPT-5.5 evaluation spend | 3 |
| 18 | **Release the 40-query gold set as a dataset** (Hugging Face, CC-BY) with the tier schema and the three-tier verification rubric. | Datasets get cited, forked and linked long after a repo is forgotten, and it makes item 3 (grow to 120) a community task instead of a personal one. | $0 | 2 |

## P1 — the gaps a senior reviewer looks for

| # | Task | Why | Cost | Hrs |
| --- | --- | --- | --- | --- |
| 6 | ~~**Upgrade off LangChain 0.1.x** (open item 9 in `KNOWN_ISSUES.md`) and delete the `pydantic.v1` shim.~~ **done** — `langchain>=0.3,<0.4` / `langchain-core>=0.3,<0.4` / `langchain-openai>=0.2,<0.4` / `langgraph>=0.2,<0.4`, `pydantic>=2` pinned, shim deleted, `main.py` graph wiring moved to the `START`/`END` idiom. The surface was smaller than feared: 5 files import the stack, and only `draft_agent.py` needed code changes (import paths + shim). Suite 395 green offline on the new stack. | An EOL-pinned framework is the first thing that ages the repo in a diff review. It is mechanical, and 395 offline tests make it safe. | $0 | 6 |
| 7 | ~~**Add a security writeup**~~ **done** — `SECURITY.md` covers `sanitize.py`, `log_redaction.py`, PDF escaping, budget guards and push protection, with an explicit limitations section. Its companion fixture (7b) shipped the evidence half. | Retrieval-origin prompt injection is the most-asked design question in agent interviews, and you already have the mitigation — it is just undocumented. This is the highest hiring-signal-per-hour item on this list. | $0 | 0 |
| 7b | ~~**Poisoned-page fixture**~~ **done** (PR #22) — `tests/test_poisoned_page.py` (11 tests) drives a hostile page through fetch → evidence → draft → judge with a stub that obeys the payload. | Turns the security writeup from prose into evidence, and it is the artifact an interviewer can read in ninety seconds. It also falsified one implication: fabricated citation numbers are dropped, but a poisoned page ranked **first** by the reranker earns a real `[1]`, so the judge and the eval — not the sanitiser — are what stand between a poisoned source and a published citation. | $0 | 2 |
| 8 | ~~**Expose the agent over MCP** (a thin stdio server wrapping the research tool).~~ **done** — `mcp_server.py`: zero-dependency JSON-RPC 2.0 stdio transport; `deep_research` (mock/live, cost-guarded, saves into `results/`) + `read_report` (sandboxed to `results/`); tool execution runs with stdout redirected so pipeline chatter cannot corrupt the frame stream; failing calls return structured `isError` results. 17 tests in `tests/test_mcp_server.py`; end-to-end subprocess probe verified (initialize → tools/list → tools/call → full report). Also fixed: `stream_cli._mock_stream` now stubs `evidence_extractor.ChatOpenAI` too — the fourth LLM entry point was attempting real network calls with the fake key (401 → retries → fallback) on every `--mock` run. | Turns "another Streamlit app" into "a capability other agents can call", and it is the interface convention the ecosystem settled on. | $0 | 5 |
| 9 | **Cut the report to a real product surface**: one command (`python -m main "query" --deep --out report.md`), one hosted demo, one GIF in the README. | Traction follows the first 20 seconds. The library is deep; the entry point is not obvious. Hosting reality: the current Streamlit Community Cloud app answers anonymous visitors with a login redirect, so the README's Live Demo link is dead to everyone who matters — either make the app public or replace the link with a recorded 60-second GIF (free, cannot rot). | $0 | 3 |
| 10 | ~~**Daily cost/latency budget enforcement**: the cost dashboard measures per-run spend; add a pre-run estimate with a hard stop (`MAX_RUN_COST_USD`).~~ **done** — `cost_guard.py`: the gate reads `MAX_RUN_COST_USD`, compares it against the estimator's **worst case** (`cost_max`, not the average — a cap the worst case can blow through is not a cap), and raises `CostCapExceeded` before any API call. Unset (default) changes nothing; a malformed or negative value **fails closed** (`ValueError`) rather than silently uncapping. Wired into all three live entry points — `main.run_research` (web app), `stream_cli.main` (CLI, exit code 2, `--mock` never gated), `mcp_server.deep_research` (refusal surfaces as a structured MCP tool error an agent can read). 18 tests in `tests/test_cost_guard.py`, including the real guard through the MCP frame protocol and boundary tests derived from the estimator's own output. | Free tiers die from surprise volume. Cost *control* is rarer than cost *reporting*. | $0 | 3 |
| 11 | **Cut `v1.0.0`** once P0 items 1–4 are green. `v0.5.0` is tagged *and* published (2026-11-18): [release](https://github.com/saksham-jain177/agentic-deep-research/releases/tag/v0.5.0), annotated tag on `0e3e156`, notes built from the CHANGELOG. | A versioned artifact reads as finished software; a branch tip reads as a hobby. But `1.0.0` at 10% eval coverage would be a number this repo's own pitch says it does not publish, so the tag is 0.5.0 and the gate is the benchmark page plus a CI comparison. | $0 | 1 |
| 23 | ~~**Answer "why not GPT-Researcher / ChatGPT Deep Research?" in the README**~~ **done** — above the problem statement, and it now leads with the measurable version of the claim (published command, coverage printed next to the score). | Every reviewer asks it and the repo gave no answer. The honest one is also the selling point: the others optimise for a readable report; this one scores its own citations on a fixed gold set and refuses to publish a number it cannot reproduce. | $0 | 0 |

## P2 — only after P0/P1 are green

| # | Task | Why | Cost | Hrs |
| --- | --- | --- | --- | --- |
| 12 | **Local model path** (Ollama) as a first-class provider, benchmarked against the API path on the same gold set. | Makes the whole project genuinely $0 to run and produces the most interesting table you could publish: quality delta vs. cost per tier. | $0 | 8 |
| 13 | **Hybrid retrieval** (BM25 + dense + RRF) behind the existing eval, so the ablation shows whether it earns its keep. | Standard practice, and you have the harness to prove the delta rather than assert it. | $0 | 5 |
| 14 | **Adaptive planning**: let the agent decide sub-queries per tier instead of a fixed template, scored on T2/T3. | The interesting research question left in this design — and the one that generalises to your day job. | $0 | 10 |
| 15 | **Human preference pair collection** on T3 (contested) outputs, published as a small dataset. | Nobody else has a free, labelled "contested-evidence" set of this kind; a dataset gets cited long after a repo is forgotten. | $0 | 8 |
| 19 | **Claim ⊨ quote entailment**: score each citation by whether the quoted span entails the claim (NLI model or a strict structured-output judge), instead of asking an LLM if the section "looks supported". | Removes the judge's ability to be impressed by fluent prose. This is the difference between an eval that deters bad changes and one that flatters them. | $0 | 5 |
| 20 | **Plan-review gate + no-progress breaker**: score retrieved documents before drafting (CRAG-style re-retrieve on irrelevant), and cap the loop when an iteration adds no new evidence. | The two failure modes that make long-running agents burn quota: a bad plan nobody challenges, and a loop that re-reads the same pages. | $0 | 6 |
| 21 | **Versioned report contract**: `schema_version` in the JSON the pipeline emits, with a test that fails on an undocumented shape change. | The eval harness, the cost dashboard and the exporters all parse the report; silent drift there is how a benchmark starts lying. | $0 | 2 |
| 22 | **SearXNG as a second search provider**, self-hosted, run against the same gold set. | Takes the Tavily quota off the critical path and buys another honest ablation: does the provider change answer quality, or only the bill? | $0 | 4 |

## Deliberately not doing

- **A new UI, a rewritten frontend, more frameworks.** The Streamlit app is
  adequate and is not what is being evaluated.
- **Chasing raw model quality.** Swapping in a bigger model moves the number
  without moving the engineering; the differentiator here is the harness.
- **Anything requiring a paid plan to demo.** If a feature needs $20/month to
  be seen working, it is a chore, not a feature.
- **Rewriting on another agent framework** (DSPy, CrewAI, AutoGen, LangGraph
  1.x big-bang). A port moves the README's dependency line and none of the
  metrics. Opt in only when a specific item above needs it.
- **More export formats, more UI themes, more provider badges.** The report
  already leaves as PDF/DOCX/MD/BibTeX/txt; every additional one is surface
  area a reviewer can break.
- **Auth, multi-tenancy, a database of users.** This is a single-user research
  tool; adding accounts turns a portfolio project into an unshipped SaaS.
- **Scaling claims (k concurrent agents, GPU inference).** Unmeasurable on free
  tiers, so it would be an unverified number on purpose. Claim the eval instead.

## Standing rule

One variable per eval run. If a change moves three metrics at once it proves
nothing, and `eval compare` exists to make that discipline the easy path.
