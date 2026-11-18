# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the versions below are
**retrospective** — no tags were published before this file, so each release entry
groups the merged pull requests that landed together on `main`. From here on, new
versions get real tags.

Measurement note: every "tests" figure is the offline suite
(`python -m pytest`, no API key required) at that point in history. The eval
numbers are cited from `results/replay-goldset.md` and carry their coverage
(4 of 40 gold queries currently have replay recordings), not as a 40-query claim.

## [Unreleased]

### Changed
- **LangChain 0.1.x → 0.3.x with native Pydantic v2** (ROADMAP item 6, KNOWN_ISSUES item 9):
  `langchain>=0.3,<0.4`, `langchain-core>=0.3,<0.4` (now pinned explicitly — `draft_agent` imports it
  directly), `langchain-openai>=0.2,<0.4`, `langgraph>=0.2,<0.4`, `pydantic>=2`. The
  `pydantic.v1` compatibility shim in `draft_agent.py` is deleted — langchain-core ≥ 0.3 accepts v2
  models as tool schemas natively — and `main.py`'s graph wiring moves from the deprecated
  `set_entry_point`/`set_finish_point` to the equivalent `START`/`END` edges. The migration surface
  was 5 files; only `draft_agent.py` needed code changes. Suite: 395 passed offline on the new
  stack (langchain 0.3.30, langchain-core 0.3.86, langgraph 0.3.34, pydantic 2.14.3).

### Added
- **Versioned report contract** (ROADMAP item 21): `report_contract.py` pins the shape of the report
  dict the pipeline emits — `SCHEMA_VERSION` ("1.0"), documented key sets, `validate()` returning
  human-readable violations — and `tests/test_report_contract.py` runs the offline mock pipeline
  end to end against it, so any undocumented shape change anywhere upstream fails the suite until
  the contract is updated and the version bumped. The contract's first run caught a real drift:
  `stream_cli._consume` and the MCP server's `_collect` were two divergent copies of the collection
  logic, one carrying raw transport keys (`event`, `index`) inside sections. Both now delegate to
  `draft_agent.collect_streamed_report` (the single construction site); sections keep their judge
  verdict as an optional `verification` key and `events` is a documented optional key.
- **Pre-run cost cap (`MAX_RUN_COST_USD`)** (ROADMAP item 10): `cost_guard.py` turns the cost
  estimator from a report into a control. When the variable is set, every live entry point — the
  web app (`main.run_research`), the CLI (`stream_cli.main`, exit code 2), and the MCP server
  (`deep_research`, refusal surfaces as a structured tool error) — refuses to start when the
  **worst-case** estimate (`cost_max`, not the average) exceeds the cap, before any API call.
  Unset (default) changes nothing; `--mock` is never gated; a malformed or negative value fails
  **closed** (`ValueError`) rather than silently uncapping. 18 tests (`tests/test_cost_guard.py`),
  including the real guard through the MCP frame protocol and cap boundaries derived from the
  estimator's own output.
- **MCP stdio server** (ROADMAP item 8): `mcp_server.py` exposes the pipeline to any AI agent over the
  Model Context Protocol — `deep_research` (mock/live, `MAX_RUN_COST_USD`-gated, optional save into
  `results/`) and `read_report` (sandboxed to `results/`, `.md` only). Zero new dependencies: the
  JSON-RPC 2.0 stdio transport is implemented in the file. Tool execution runs with stdout redirected
  to stderr so pipeline chatter cannot corrupt the frame stream; failing tool calls return structured
  `isError` results and the server survives. 17 tests (`tests/test_mcp_server.py`), plus an
  end-to-end subprocess probe (initialize → tools/list → tools/call → full report).

### Fixed
- **`--mock` was not fully offline.** `stream_cli._mock_stream` stubbed three LLM entry points but
  not `evidence_extractor.ChatOpenAI` — the fourth — so every mock run attempted real network calls
  with the fake key (401 → two retries → heuristic fallback). All four are now stubbed; mock runs
  no longer touch the network.
- **Eval F5 — source matching can no longer credit a claim to the wrong source.** `find_reference_text`
  matched by exact URL, then by any source whose URL contained the reference's domain (first hit
  wins), then by the reference number's position in the source list when the URL matched nothing.
  Two of those three grade claims against pages the report may never have cited: a domain shared
  by two gathered sources is ambiguous, and a URL that matches nothing means the reference points
  outside the source list. The matcher now normalises URLs (scheme/`www`/trailing slash) for the
  exact match, resolves a domain only when exactly one gathered source carries it, and falls back
  to position only when the reference carries no URL at all — every other case fails the pair
  honestly instead of guessing.
- **Eval F8 — the Cohen's κ gate is enforced in the run path.** `eval run` gains `--calibration
  labels.jsonl`: a judge whose κ against human labels is below 0.6 fails the run (exit 1) unless
  `--allow-uncalibrated` is passed, and the calibration is recorded in run metadata
  (`judge_calibration`). The rendered report states κ against n human pairs, or marks the run
  **UNCALIBRATED** in a banner when no labels were supplied — previously `calibrate` printed a
  warning that nothing downstream read.

### Added
- **Replay eval as a PR comment** (ROADMAP P0 item 5, the gate that was standing between this
  project and "every PR is a documented experiment"). `.github/workflows/eval-comment.yml` checks
  out `main` and the PR head, runs `python -m eval run --mode replay` in both, and
  `scripts/eval_ci_comment.py` posts — or edits, on later pushes — one comment carrying the delta
  table. Three properties the table alone would not have: replay is deterministic (fixed fixtures,
  heuristic judge), so there is **no tolerance band** and a non-zero adverse delta is named as the
  code's doing; a branch that changes *which* queries are scored is labelled **not comparable as an
  ablation** instead of printing a delta that mixes two variables; and rows the harness cannot
  measure are labelled as such — latency and cost come from the recordings, so they move only when
  fixtures are re-recorded, never because this PR's pipeline got slower. It is a comment, not a
  check, so a deliberate trade-off still merges with the number visible.
  `tests/test_eval_ci_comment.py` (10 tests, offline, and the posting path refuses to run without
  the Actions token rather than reaching for the network).

### Tests
- 336 → 377 offline tests (eval F5/F8 regressions, MCP server protocol suite).

## [0.5.0] — 2026-11-18

First tagged release. Everything between `0.4.0` and this line is what PRs #18, #19, #21 and #22
merged into `main`; the tag is annotated with the reproduction command and the current eval coverage.

**Why 0.5.0 and not 1.0.0.** 4 of the 40 gold queries have replay recordings, so the headline
numbers in the README describe a tenth of the benchmark, and the LangChain 0.1.x pin is still in
`requirements.txt`. `1.0.0` should be the release where `eval compare` runs on every PR, the gold
set is re-recorded at full coverage, and the benchmark page exists — that is ROADMAP P0 items 1–5.
A version number is a claim, and this project's claim is that it does not publish numbers it cannot
reproduce.

### Added
- **Adversarial poisoned-page fixture** (`tests/fixtures/poisoned_wheat_page.html`,
  `tests/test_poisoned_page.py`, 11 tests, offline). A page carrying instruction-shaped text is
  pushed through fetch → evidence → draft → judge with a stub model that obeys the payload. It pins
  two properties and falsifies a third assumption: an invented citation number (`[7]`) is stripped
  and never reaches the bibliography, and no dangling citation survives the run; but the same page,
  ranked first by the real cross-encoder, acquires a legitimate `[1]` that the drafting layer has no
  basis to refuse. `SECURITY.md` gained that limitation. Suite: 336 offline tests.

### Landed since the last release
- PRs #18 (offline eval harness + review fixes), #19 (eval scoring validity) and #21 (citation
  numbering, weighted section budgets, the keyless `--mock` fix, `SECURITY.md`, `CHANGELOG.md`,
  `ROADMAP.md`) are merged into `main`.

### Fixed
- **Bibliography numbering now matches the inline markers.** The draft node printed
  `[1]` on every reference and let the body cite numbers the reference list did not
  contain. A per-run citation registry assigns one position per source, a single-pass
  renumbering pass rewrites the body, and dangling markers are detected. Covered by
  `tests/test_citation_numbering.py`. (commit `019b5a3`)
- **Eval scoring counts every failure.** A claim–citation pair that failed any of the
  three verification tiers is now in the denominator, so `citation_accuracy` cannot be
  inflated by skipping hard pairs; `content` is no longer labelled as a captured field
  where the recording did not capture it. (commit `336bddf`)
- **Per-section word budgets are weighted by role.** `target_word_count // len(sections)`
  asked a 1000-word deep report for a 166-word abstract and a 166-word literature
  review; budgets now follow each section's job (Abstract 80, Key Findings 270 at the
  deep setting). Streaming and non-streaming paths share one rule.
  `tests/test_section_budget.py`. (commit `26280c3`)
- **Unattempted eval queries report as coverage, not errors.** A query with no replay
  recording is marked `skipped:` and excluded from the error count and the headline
  denominator; the table prints `Queries scored 4 (10% of gold set)`. A partial replay
  can no longer be misread as a 40-query benchmark. (commit `26280c3`)
- **Confirmed code-review findings**: a `**kwargs` bug in tool forwarding, the
  excluded-domain filter, PDF markup escaping in the Reportlab export, semantic-cache
  TTL, and staleness handling. (commit `52efdfb`)

### Removed
- The unused lambda `Tool` wrapper around `research_web` (and its `langchain.tools`
  import). Nothing called it once the workflow used `research_web` directly, and it
  depended on undocumented kwarg forwarding through `Tool.run`.

### Tests
- 325 → 336 offline tests (up from 282). `os.environ` is snapshotted and restored per test,
  so a `load_dotenv()` in one test no longer decides another's verdict; the cost
  dashboard's unlabelled-model default is pinned. (commit `0a10bbb` at 325; the 11
  poisoned-page tests above land on top of it in PR #22)

### Docs
- `ROADMAP.md` — taskpad ordered by impact per unit of zero-cost effort, an
  external-evidence tier, and an explicit list of things this project will not do.
- README clone command, `cd`, and footer link updated for the renamed repository; the
  stale discussions link is gone; test counts synced.
- `SECURITY.md` — threat model, the mitigations that exist in code, and their
  limitations.
- `CHANGELOG.md` — this file.

## [0.4.0] — 2026-08-24

### Added
- Offline evaluation harness (`eval/`) with a 40-query stratified gold set
  (verifiable fact / synthesis / contested / must-abstain), three-tier
  claim–citation verification, Cohen's κ judge calibration, replay fixtures, and
  `eval compare` for ablations — `python -m eval {validate,run,report,compare,calibrate}`.
- LLM-as-a-Judge verification node scoring every generated section against its
  sources (supported / partial / unsupported plus citation coverage). PR #10.
- Contradiction detection and surfacing: conflicting claims across sources are exposed
  rather than averaged away. PRs #11, #14.
- Streaming report generation with incremental per-section verification. PR #13.
- Multilingual verification and contradiction detection. PR #14.
- Per-run cost dashboard: run-summary JSON and a static HTML view. PR #15.
- README "Output Quality Guarantees" section, CI badge, corrected architecture
  diagram. PR #17.

### Fixed
- Report language is threaded through the streaming verification path; the stream CLI
  uses `research_web` directly. PR #16.

### Tests
- Suite at 180+ offline tests; CI gating on every PR.

## [0.3.0] — 2026-08-24

### Added
- **Tier 1 quality upgrades** (PR #5): evidence-table extraction that grounds section
  drafting, citation grounding, section-level retry, parallel sub-query search, and
  cross-encoder reranking of retrieved pages.
- **Quick wins** (PR #6): parallel sub-queries, cross-encoder rerank, section-level
  retry landed on `main` ahead of the full tier.

### Changed
- Explicit timeouts and retry budgets on LLM and Tavily calls; module logger instead of
  `print` for vector-store status. PR #8.
- `LLM_MAX_OUTPUT_TOKENS` caps generation output. PR #8.
- Removed the unused `generate_section` and orphaned shallow-mode prompts. PR #8.

### Fixed
- Source dictionary keys mapped correctly in `format_citation`. PR #8.

## [0.2.0] — 2026-02-05

### Added
- Cost citation tracking, Streamlit UX hardening. PR #1.

### Fixed
- Report rendering in the UI and in exports. PRs #2, #3, #4.

## [0.1.0] — initial

Two-agent LangGraph research pipeline: Tavily web search, ChromaDB storage, section
drafting, Streamlit UI, PDF/DOCX/MD/BibTeX/txt export.
