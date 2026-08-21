# Known Issues / Cleanup Backlog

Issues identified during the startup-fix pass (commit e59b0c2). None are
blocking; the app runs. Ordered roughly by impact.

## Functional gaps

1. **Vector memory can never activate.**
   `requirements.txt` includes `chromadb` but **not** `sentence-transformers`,
   which `vector_store.py` imports at module level. The import always fails,
   so the sidebar always shows "Install chromadb and sentence-transformers".
   Fix: add `sentence-transformers` to requirements (heavy dependency —
   consider making it an optional extra).

2. **`check_openrouter_status()` runs on every page render** (sidebar) and
   again inside `generate_pdf()` / `generate_docx()`. That's a live network
   call per rerun — slows every widget interaction, and a transient timeout
   bakes "OpenRouter Status: Down" into exported PDFs. Fix: cache it
   (`@st.cache_data(ttl=...)`) and drop the check from the export path.

3. **"Research Data" section renders twice** in the results view
   (once as "View External Source Snippets" expander, again as a duplicate
   `### Research Data` block further down). Remove one.

4. **`research_agent.py` writes `research_data.json` to CWD on every search**
   as a debug side effect. Harmless locally (gitignored), but on Streamlit
   Cloud the filesystem is ephemeral/read-only-ish. Fix: gate behind an env
   flag or remove.

## Minor / polish

5. **Benchmark error message logic** (`app.py`, sidebar): the
   `if 'bench' in locals() and bench is None` branch is always reachable when
   `ctx_len` is missing, so the "Benchmark failed" error can appear without
   the user ever clicking the button.

6. **`_validate_tavily_key`** performs a real Tavily search ("ping") just to
   validate the key. Cached for 5 min, but it burns API credits on every
   cache miss. A cheaper endpoint or format check would do.

7. **Feedback form** swallows all SMTP errors with a generic message and the
   credentials model (Gmail app-password in env) is fragile. Consider
   removing or replacing with a webhook.

8. **Dead code**: `preprocess_references()`, `format_references_section()`,
   `_render_small_text()` in `app.py` are never called. One of them contained
   a latent 2-arg bug (now fixed). Delete them in a dedicated cleanup commit.

## Structural (larger, needs its own effort)

9. **LangChain 0.1.x is EOL-pinned** (`>=0.1.0,<0.2.0`). The `pydantic.v1`
   shim in `draft_agent.py` is a deliberate compatibility fix, not a
   long-term home. Upgrading to LangChain 0.3.x + Pydantic v2 native would
   remove the shim, but touches `research_agent.py`, `draft_agent.py`, and
   `main.py` — schedule as its own change with tests.

10. **`fetch_openrouter_models()` runs on every script rerun** before the
    sidebar renders (cached 10 min, but the first paint of a fresh session
    blocks on it). Consider lazy-loading behind a spinner or session cache.
