# Contributing

Thanks for your interest in improving this project!

## Setup

```bash
python -m venv .venv
# Windows:
.venv\Scripts\pip install -r requirements.txt
# macOS/Linux:
.venv/bin/pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your own `TAVILY_API_KEY` and
`OPENROUTER_API_KEY`. Never commit `.env`.

## Running the tests

```bash
pytest tests/ -v --tb=short
pytest tests/ -v -m smoke   # integration-level smoke tests only
```

The same commands run in CI (`.github/workflows/test.yml`, Python 3.11) on
every push to `main`/`harden/*` and on every pull request.

**If pytest fails at startup with `ModuleNotFoundError: No module named
'langsmith.pytest_plugin'`**, your environment has two `langsmith`
distributions visible at once (for example a venv created with
`--system-site-packages` that pins an older `langsmith` than the global
interpreter). The global dist-info declares the `langsmith_plugin` pytest11
entry point, but the import resolves to the shadowing copy, which predates
that module. Block just that plugin and everything else stays loaded:

```bash
pytest tests/ -q -p no:langsmith_plugin
```

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` also produces a green run, but it disables
all plugins and can mask a real plugin failure - use it only as a fallback.

## Pull requests

1. Create a feature branch (`fix/...` or `feat/...`) from `main`.
2. Keep changes focused; add or update tests for behavior you change.
3. Run the full suite locally before pushing.
4. Open the PR against `main`; CI must pass before merge.

## Conventions

- Python 3.11+; standard library preferred over new dependencies — check
  `requirements.txt` before adding one, and remove deps that are no longer
  imported.
- Modules are plain functions/classes (no framework); keep the LangGraph
  wiring in `main.py` and Streamlit UI in `app.py`.
- Web content is untrusted: anything fetched from the web passes through
  `sanitize.py` before reaching an LLM prompt, and must be HTML-escaped
  before being embedded in reportlab markup (PDF export).
