"""Regression tests for the code-review findings fixed on fix/review-findings.

Each test maps to one confirmed finding that was re-verified against main
(111ebf3) before fixing. See the PR description for the full classification.
"""

import ast
import datetime
import html
import io
import json
import re
import time
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# research_agent: domain filter and cache-flag handling
# ---------------------------------------------------------------------------

from research_agent import _is_excluded_domain, _strip_cache_flags

DEFAULT_EXCLUDED = {
    "reddit.com", "x.com", "twitter.com", "tiktok.com",
    "pinterest.com", "instagram.com",
}


def test_domain_filter_does_not_exclude_substring_matches():
    """x.com must not exclude every domain merely ending in 'x.com'."""
    assert not _is_excluded_domain("https://www.netflix.com/watch", DEFAULT_EXCLUDED)
    assert not _is_excluded_domain("https://wix.com/", DEFAULT_EXCLUDED)
    assert not _is_excluded_domain("https://dropbox.com/home", DEFAULT_EXCLUDED)


def test_domain_filter_excludes_domain_and_its_subdomains():
    assert _is_excluded_domain("https://reddit.com/r/xyz", DEFAULT_EXCLUDED)
    assert _is_excluded_domain("https://www.reddit.com/r/xyz", DEFAULT_EXCLUDED)
    assert _is_excluded_domain("https://old.reddit.com/r/xyz", DEFAULT_EXCLUDED)
    assert _is_excluded_domain("https://x.com/user", DEFAULT_EXCLUDED)
    assert _is_excluded_domain("https://www.x.com/user", DEFAULT_EXCLUDED)


def test_strip_cache_flags_counts_before_stripping():
    data = [
        {"title": "a", "from_cache": True},
        {"title": "b"},
        {"title": "c", "from_cache": True},
    ]
    count = _strip_cache_flags(data)
    assert count == 2
    assert all("from_cache" not in item for item in data)


# ---------------------------------------------------------------------------
# draft_agent: think-tag cleaning must not destroy Markdown structure
# ---------------------------------------------------------------------------

from draft_agent import clean_think_tags


def test_clean_think_tags_preserves_markdown_newlines():
    # Tag literals built by concatenation so this file stays parseable
    # even when processed by tooling that strips think-tag markup.
    open_tag = "<" + "think>"
    close_tag = "<" + "/think>"
    text = (
        "Intro line.\n"
        + open_tag + "reasoning that must disappear" + close_tag + "\n"
        "\n"
        "- item 1\n"
        "- item 2\n\n"
        "Paragraph two."
    )
    out = clean_think_tags(text)
    assert "reasoning that must disappear" not in out
    assert "- item 1\n- item 2" in out, "list newlines must survive"
    assert "\n\nParagraph two." in out, "paragraph breaks must survive"


def test_clean_think_tags_removes_stray_open_close_tags():
    out = clean_think_tags("keep me")
    assert out == "keep me"


# ---------------------------------------------------------------------------
# cost_estimator: honest staleness signal
# ---------------------------------------------------------------------------

from cost_estimator import estimate_research_cost


def test_pricing_staleness_reports_unknown_not_fake_fresh():
    result = estimate_research_cost(
        "test query", deep_research=False, target_word_count=500
    )
    assert isinstance(result["pricing_staleness_days"], int)
    assert result["pricing_staleness_days"] == -1


# ---------------------------------------------------------------------------
# main: deep-research/language flags must reach research_web, cache TTL
# ---------------------------------------------------------------------------

import main as main_module


def test_fetch_research_data_passes_flags_positionally(monkeypatch):
    """Tool.run() swallows unknown kwargs, so the call must be positional."""
    seen = {}

    def fake_research_web(query, deep_research=False, language="en"):
        seen["args"] = (query, deep_research, language)
        return [{"title": "t", "content": "c", "url": "https://example.com"}]

    monkeypatch.setattr(main_module, "research_web", fake_research_web)
    # Unique query so the on-disk joblib cache can never serve a stale entry.
    query = f"unique-{uuid.uuid4()}"
    result = main_module.fetch_research_data(query, True, "english", cache_bucket=0)
    assert seen["args"] == (query, True, "en")
    assert result[0]["title"] == "t"


def test_cache_bucket_zero_disables_ttl(monkeypatch):
    monkeypatch.setattr(main_module, "CACHE_TTL_HOURS", 0.0)
    assert main_module._cache_bucket() == 0


def test_cache_bucket_advances_with_ttl(monkeypatch):
    monkeypatch.setattr(main_module, "CACHE_TTL_HOURS", 24.0)
    assert main_module._cache_bucket() == int(time.time() // (24 * 3600))


# ---------------------------------------------------------------------------
# app.py functions, tested without importing the module
# (app.py runs Streamlit UI code at import time, so we exec the extracted
# function definitions in a namespace holding only their real dependencies)
# ---------------------------------------------------------------------------

def _extract_app_functions(*names):
    tree = ast.parse((PROJECT_ROOT / "app.py").read_text(encoding="utf-8"))
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors as rl_colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    namespace = {
        "io": io,
        "json": json,
        "re": re,
        "html": html,
        "datetime": datetime,
        "letter": letter,
        "colors": rl_colors,
        "SimpleDocTemplate": SimpleDocTemplate,
        "Paragraph": Paragraph,
        "Spacer": Spacer,
        "PageBreak": PageBreak,
        "getSampleStyleSheet": getSampleStyleSheet,
        "ParagraphStyle": ParagraphStyle,
    }
    picked = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert picked, f"none of {names} found in app.py"
    exec(compile(ast.Module(body=picked, type_ignores=[]), "app.py", "exec"), namespace)
    return namespace


def test_pdf_escape_neutralizes_markup():
    ns = _extract_app_functions("_pdf_escape")
    raw = "<" + "script" + ">" + "&" + "<" + "/script" + ">"
    expected = (
        "&" + "lt;" + "script" + "&" + "gt;"
        + "&" + "amp;"
        + "&" + "lt;" + "/script" + "&" + "gt;"
    )
    assert ns["_pdf_escape"](raw) == expected


def test_generate_pdf_survives_hostile_web_content():
    """Raw web content with tags/ampersands must not break reportlab."""
    ns = _extract_app_functions(
        "on_page", "format_reference_for_pdf", "_pdf_escape", "generate_pdf"
    )
    summary = json.dumps({
        "sections": [
            {
                "title": "Results <b>injected</b>",
                "content": (
                    "Bad <script>alert('x')</script> & <img src=x onerror=alert(1)> "
                    "**bold** text\n\nmore & <td>content</td>"
                ),
            },
        ],
        "references": ["(2024, Smith). Retrieved from https://example.com/a<b>&c"],
    })
    data = [{"title": "T <script>", "content": "C & <div>", "url": "https://example.com"}]
    buffer = ns["generate_pdf"]("Q <&>", data, summary, deep_research=False)
    assert buffer.getvalue(), "PDF should build without raising"


def test_bench_accumulate_chunk_uses_provider_usage():
    ns = _extract_app_functions("_bench_accumulate_chunk")
    chars, usage = ns["_bench_accumulate_chunk"](
        {"usage": {"completion_tokens": 42}}, 0, None
    )
    assert (chars, usage) == (0, 42)


def test_bench_accumulate_chunk_counts_delta_chars():
    ns = _extract_app_functions("_bench_accumulate_chunk")
    chunk = {
        "choices": [
            {"delta": {"content": "hello world"}},
            {"delta": {"content": "!"}},
        ]
    }
    chars, usage = ns["_bench_accumulate_chunk"](chunk, 0, None)
    assert chars == 12
    assert usage is None


# ---------------------------------------------------------------------------
# vector_store: fresh installs must persist, not use in-memory client
# ---------------------------------------------------------------------------

def test_vector_store_persists_on_fresh_dir(tmp_path):
    chromadb = pytest.importorskip("chromadb")
    from vector_store import ResearchVectorStore

    persist_dir = tmp_path / "chroma_db"
    store = ResearchVectorStore(persist_dir=str(persist_dir), enable_reranker=False)
    # PersistentClient is a factory function, not a class - assert on the
    # observable behavior: persistent settings + sqlite db on disk.
    assert store.client.get_settings().is_persistent is True
    assert persist_dir.exists(), "PersistentClient should create the directory"
    assert (persist_dir / "chroma.sqlite3").exists(), "persistent client should create its sqlite db"


# ---------------------------------------------------------------------------
# Polish pass: port handling, benchmark honesty, bounded cache
# ---------------------------------------------------------------------------

def test_domain_filter_ignores_port_and_credentials():
    """netloc may be user@host:port; the exclusion list targets the host."""
    assert _is_excluded_domain("https://reddit.com:443/r/xyz", DEFAULT_EXCLUDED)
    assert _is_excluded_domain("https://user:pw@www.reddit.com/r/xyz", DEFAULT_EXCLUDED)
    assert not _is_excluded_domain("https://netflix.com:443/watch", DEFAULT_EXCLUDED)


def test_bench_summary_returns_none_without_provider_data():
    """No usage block and no deltas means no throughput - not '1 token/s'."""
    ns = _extract_app_functions("_bench_summary")
    assert ns["_bench_summary"](1.0, 0, None, 0.5) is None
    assert ns["_bench_summary"](1.0, 0, 0, 0.5) is None


def test_bench_summary_prefers_provider_usage():
    ns = _extract_app_functions("_bench_summary")
    result = ns["_bench_summary"](2.0, 400, 50, 2.0)
    assert result == {"latency_s": 2.0, "throughput_tps": 25.0, "tokens": 50}


def test_bench_summary_estimates_from_chars_when_usage_absent():
    ns = _extract_app_functions("_bench_summary")
    result = ns["_bench_summary"](1.0, 40, None, 2.0)
    assert result["tokens"] == 10
    assert result["throughput_tps"] == 5.0


def test_cache_prune_is_skipped_when_ttl_disabled(monkeypatch):
    """RESEARCH_CACHE_TTL_HOURS=0 keeps entries forever, so never prune."""
    calls = []
    monkeypatch.setattr(main_module, "CACHE_TTL_HOURS", 0)
    monkeypatch.setattr(main_module.memory, "reduce_size", lambda **kw: calls.append(kw))
    main_module._prune_cache()
    assert calls == []


def test_cache_prune_runs_once_and_swallows_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "CACHE_TTL_HOURS", 24)
    monkeypatch.setattr(main_module, "_cache_pruned", False)
    monkeypatch.setattr(main_module.memory, "reduce_size", lambda **kw: calls.append(kw))
    main_module._maybe_prune_cache()
    main_module._maybe_prune_cache()
    assert len(calls) == 1, "pruning must not run on every cache miss"
    assert "age_limit" in calls[0]

    def boom(**kwargs):
        raise RuntimeError("joblib cannot prune here")

    monkeypatch.setattr(main_module, "_cache_pruned", False)
    monkeypatch.setattr(main_module.memory, "reduce_size", boom)
    main_module._maybe_prune_cache()  # must not raise
