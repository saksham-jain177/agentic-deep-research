# Security policy

This is a single-user research tool: a Streamlit UI and CLI that fetches web pages you ask it to
research, sends their text to an LLM provider, and writes a report to disk. That shape has one
threat that matters more than the rest — **content fetched from the web is untrusted input that
ends up inside a model prompt** — so that is where most of the design work went.

## Reporting a vulnerability

Open a [private security advisory](https://github.com/saksham-jain177/agentic-deep-research/security/advisories/new)
rather than a public issue, so a fix can land before details are indexed by search engines. If you
would rather email, use the address on the GitHub profile. Expect a reply within a few days; this is
an unsupported personal project, so "a fix" means a patch and a release note, not an SLA.

## Threat model — what this software does and does not promise

**It is designed for:** one operator, on their own machine or their own Streamlit instance, using
their own API keys, researching topics they chose.

**It is not designed for:** multi-tenant hosting, untrusted users submitting queries, or
processing the output without a human reading it. It has no authentication, no per-user
isolation and no output signing. Do not put an instance somewhere anonymous users can reach.

## Mitigations that exist in the code

| Risk | Mitigation | Where |
| --- | --- | --- |
| Prompt injection through fetched web content | Every retrieved document's title/content is run through a neutraliser before it reaches a prompt: instruction-shaped text, role markers and control tokens are defanged, and non-text nodes are dropped. | [`sanitize.py`](sanitize.py), called on the results of every search in [`research_agent.py`](research_agent.py) |
| Secrets in logs or UI state | API keys and bearer tokens are redacted from log lines and from anything rendered into session state. | [`log_redaction.py`](log_redaction.py) |
| Malicious markup in web content reaching an exporter | Source titles and body text are HTML/XML-escaped before being handed to Reportlab, so a page containing `<…>` markup cannot break or inject into the PDF; DOCX/MD/txt exports route through the same reference formatting. | `_pdf_escape` / `_reference_line` in [`app.py`](app.py), pinned by `tests/test_review_fixes.py` |
| Unbounded spend on a free tier | Per-run token budget guard, output-token caps, and a pre-call cost estimate; a per-stage cost record is written with every run. | `draft_agent.py` (budget guard), `cost_estimator.py`, `cost_dashboard.py` |
| A runaway research loop | Query variants are bounded, the number of retrieved documents is capped per mode, and the evaluation harness caps claim–citation pairs so a single long report cannot dominate a metric. | `research_agent.py`, `eval/runner.py` |
| Secret leakage through git | `.env` is ignored; GitHub **secret scanning with push protection is enabled** on this repository. No key, and no `.env`, is committed in history. | repository settings, verified 2026-11-17 |
| Feedback form used as a mail relay | The optional feedback form is **off by default** (`FEEDBACK_ENABLED`), rate-limited to one submission per hour per session, and caps message length. | `app.py` |
| Debug artifacts capturing raw results | The raw research dump is behind `DEBUG_DUMP_RESEARCH` and off by default, so ordinary runs write nothing beyond the vector store and the report you asked for. | `research_agent.py` |

Tests for the injection, escaping, redaction and budget paths are offline and keyless:
`python -m pytest` runs the whole suite without any credential present.

## Known limitations — read these before trusting the mitigations

- **Sanitisation is heuristic, not a guarantee.** It removes the instruction patterns this project
  has actually observed. A page can still influence a report by being persuasive; a human reads the
  output before acting on it, and that is part of the design, not a fallback.
- **Domain filtering is a blocklist.** `EXCLUDED_DOMAINS` removes social platforms; it is not an
  allowlist and should not be treated as a trust decision.
- **The LLM-as-a-Judge verification node is not a security control.** It measures whether a
  citation supports a claim; it is calibrated against human labels with Cohen's κ
  (`python -m eval calibrate`) and it can be wrong in the same direction the report is wrong.
- **Local persistence is unencrypted.** ChromaDB data under `chroma_db/` and any report you export
  live on disk in the clear. On a shared machine, treat that directory as yours.
- **The vector store and cache are per-machine.** Enabling "memory" means past queries and
  retrieved text stay on that host; nothing is synced anywhere, which is the point, but it also
  means wiping your history is a file operation.
- **Dependencies resolve from public indexes and are version-ranged, not hash-pinned.** Install in
  an isolated virtualenv and run `pip-audit -r requirements.txt` if that matters in your
  environment.

## Privacy summary

Queries, retrieved page text and generated reports stay on your machine plus whatever you
deliberately send to your configured LLM and search providers. No telemetry, no analytics, no
account system, and no third-party request is made except the two you configured (search provider
and model provider).
