# Known Issues / Cleanup Backlog

Status after the cleanup pass (see git log for the fix commits).
Items marked ✅ are resolved; ❌ items remain open.

## Resolved ✅

1. ~~**Vector memory can never activate** — `sentence-transformers` missing
   from requirements.~~ Added `sentence-transformers>=2.2.0`.

2. ~~**`check_openrouter_status()` ran a live network call on every rerun**
   (and baked "Down" into exported PDFs).~~ Now cached (`@st.cache_data`,
   ttl=120) and removed from the PDF/Word export path entirely.

3. ~~**"Research Data" section rendered twice.**~~ Duplicate block removed;
   the expander version with cleaned content and source links is kept.

4. ~~**`research_data.json` written to CWD on every search.**~~ Now gated
   behind `DEBUG_DUMP_RESEARCH=true`.

5. ~~**Benchmark error could show without user action.**~~ Error now only
   renders in the same run where the button was actually clicked.

6. ~~**Tavily key validation burned API credits** with a real search per
   cache miss.~~ Replaced with an offline format check (`tvly-` prefix).

7. ~~**Feedback form swallowed SMTP errors.**~~ Now surfaces the exception
   type alongside the generic message.

8. ~~**Dead code**: `preprocess_references()`, `format_references_section()`,
   `_render_small_text()` deleted.~~ Note: `format_reference_for_pdf` was
   initially misidentified as dead but is live in both export fallback paths —
   it is kept, restored to its original 1-arg signature.

10. ~~**Model-list fetch blocked first paint silently.**~~ Wrapped in a
    spinner; still cached (ttl=600).

## Open ❌

9. **LangChain 0.1.x is EOL-pinned** (`>=0.1.0,<0.2.0`). The `pydantic.v1`
   shim in `draft_agent.py` is a deliberate compatibility fix, not a
   long-term home. Upgrading to LangChain 0.3.x + Pydantic v2 native would
   remove the shim, but touches `research_agent.py`, `draft_agent.py`, and
   `main.py` — schedule as its own change with tests.
