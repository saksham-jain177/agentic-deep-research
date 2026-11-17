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

---

## P0 — finish what is measurable, before adding anything

Do these first. Each one converts work already in the repo into a number you
can publish, which is the only thing that distinguishes this project from the
other deep-research clones.

| # | Task | Why | Cost | Hrs |
| --- | --- | --- | --- | --- |
| 1 | **Re-record the live eval fixtures** with the current build (`python -m eval run --mode live --record-dir eval/replays`) and commit the before/after table. | Coverage is 4 of 40 (`eval/replays/` holds T1-01, T1-12, T2-01, T4-01), so every headline number describes a tenth of the gold set. Until the fix is re-measured you cannot claim it. ~28 searches + a few hundred completions on free tiers. | ~$0 | 2 |
| 2 | **Close eval findings F5 and F8.** F5: source matching is positional/substring, so a claim can be credited to the wrong source — an over-count, the one failure mode a benchmark must not have. F8: Cohen's κ is computed but never enforced in the run path, so `calibrate`'s gate is decorative. | The harness's credibility is the project's credibility. | $0 | 4 |
| 3 | **Grow the gold set from 40 to ~120 queries**, keeping the T1–T4 stratification. | 40 is a regression detector, not a capability claim (the README says so honestly). At 120 with the same tiers you can publish a per-tier table that survives scrutiny. Source queries from HotpotQA/SimpleQA/FRAMES public samples — free, licensed, and already curated by others. | $0 | 6 |
| 4 | **Publish a benchmark page**: one Markdown file per release with the metric table, the exact command to reproduce it, the commit SHA, and the fixture set. | This is the artifact a reviewer actually reads. Nobody runs your code first; they read your numbers and your method. | $0 | 2 |
| 5 | **Wire `eval compare` into CI as a comment**, not a gate: on each PR, replay the fixtures and post the delta table. | Makes "no regression" mechanical instead of a claim, and every PR becomes a documented experiment. Runs offline, ~2 min. | $0 | 3 |

## P0b — measured against someone else's ruler

Every number in this README was produced by your harness, on your queries, with
your judge. That is the weakest kind of evidence and reviewers know it. These
three items cost about a weekend and change the class of the claim.

| # | Task | Why | Cost | Hrs |
| --- | --- | --- | --- | --- |
| 16 | Run **[DeepResearch Bench](https://deepresearch-bench.github.io/)** (ICLR 2026: 100 PhD-level tasks across 22 fields, RACE + FACT scoring) against the agent — start with a 10-task pilot, free-tier models. | It is the one public benchmark built for exactly this genre of agent, with a leaderboard others are on. Publishing the split plus the exact config turns "self-reported" into "reproducible by a stranger". | ~$0–5 (only the judge may need credits) | 6 |
| 17 | **Submit the run to the leaderboard** if the pilot is respectable. | Being listed beside named systems is a credential; a star count is a popularity contest. | $0 | 2 |
| 18 | **Release the 40-query gold set as a dataset** (Hugging Face, CC-BY) with the tier schema and the three-tier verification rubric. | Datasets get cited, forked and linked long after a repo is forgotten, and it makes item 3 (grow to 120) a community task instead of a personal one. | $0 | 2 |

## P1 — the gaps a senior reviewer looks for

| # | Task | Why | Cost | Hrs |
| --- | --- | --- | --- | --- |
| 6 | **Upgrade off LangChain 0.1.x** (open item 9 in `KNOWN_ISSUES.md`) and delete the `pydantic.v1` shim. | An EOL-pinned framework is the first thing that ages the repo in a diff review. It is mechanical, and 317 offline tests make it safe. | $0 | 6 |
| 7 | **Add a security writeup** (`SECURITY.md` + a short doc) covering the paths already built: `sanitize.py` prompt-injection neutralisation of fetched HTML, `log_redaction.py`, PDF escaping, offline key validation. Include an adversarial test fixture that shows a poisoned page failing to steer the report. | Retrieval-origin prompt injection is the most-asked design question in agent interviews, and you already have the mitigation — it is just undocumented. This is the highest hiring-signal-per-hour item on this list. | $0 | 4 |
| 8 | **Expose the agent over MCP** (a thin stdio server wrapping the research tool). | Turns "another Streamlit app" into "a capability other agents can call", and it is the interface convention the ecosystem settled on. | $0 | 5 |
| 9 | **Cut the report to a real product surface**: one command (`python -m main "query" --deep --out report.md`), one hosted demo, one GIF in the README. | Traction follows the first 20 seconds. The library is deep; the entry point is not obvious. Hosting reality: the current Streamlit Community Cloud app answers anonymous visitors with a login redirect, so the README's Live Demo link is dead to everyone who matters — either make the app public or replace the link with a recorded 60-second GIF (free, cannot rot). | $0 | 3 |
| 10 | **Daily cost/latency budget enforcement**: the cost dashboard measures per-run spend; add a pre-run estimate with a hard stop (`MAX_RUN_COST_USD`). | Free tiers die from surprise volume. Cost *control* is rarer than cost *reporting*. | $0 | 3 |
| 11 | **Release v1.0.0 with a CHANGELOG and tags.** | A versioned artifact reads as finished software; a branch tip reads as a hobby. | $0 | 1 |
| 23 | **Answer "why not GPT-Researcher / ChatGPT Deep Research?" in the README**, in three lines next to the badge row. | Every reviewer asks it and the repo currently gives no answer. The honest one is also the selling point: the others optimise for a readable report; this one scores its own citations on a fixed gold set and refuses to publish a number it cannot reproduce. | $0 | 1 |

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
