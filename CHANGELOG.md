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
