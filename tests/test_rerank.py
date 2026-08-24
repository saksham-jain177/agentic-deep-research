"""Tests for cross-encoder reranking of fresh Tavily results."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import vector_store


@pytest.fixture
def sample_docs():
    return [
        {"title": "A", "content": "irrelevant content about cooking", "url": "https://a.com"},
        {"title": "B", "content": "climate change impacts on crops", "url": "https://b.com"},
        {"title": "C", "content": "another off-topic note", "url": "https://c.com"},
    ]


def test_rerank_documents_sorts_and_truncates(sample_docs):
    fake_reranker = MagicMock()
    # Scores: doc0 -> 0.1, doc1 -> 0.9, doc2 -> 0.5
    fake_reranker.predict.return_value = [0.1, 0.9, 0.5]
    with patch.object(vector_store, "get_standalone_reranker", return_value=fake_reranker):
        ranked = vector_store.rerank_documents("climate change", sample_docs, top_k=2)

    assert [d["title"] for d in ranked] == ["B", "C"]
    assert ranked[0]["rerank_score"] == pytest.approx(0.9)
    assert len(ranked) == 2
    # Original documents must not be mutated (copies are returned)
    assert "rerank_score" not in sample_docs[0]


def test_rerank_documents_empty_input():
    assert vector_store.rerank_documents("q", [], top_k=5) == []


def test_rerank_documents_zero_top_k(sample_docs):
    assert vector_store.rerank_documents("q", sample_docs, top_k=0) == []


def test_research_web_reranks_fresh_results(monkeypatch):
    """research_web should rerank fresh results via rerank_documents."""
    import research_agent

    monkeypatch.setenv("RERANK_TOP_K", "2")

    def fake_search(query, max_results=5, timeout=60, **kwargs):
        n = int(query[-1])  # distinct docs per query variant
        return {
            "results": [
                {"title": f"doc {query}-{i}", "content": f"content {n}-{i}", "url": f"https://ex{i}{n}.com"}
                for i in range(3)
            ]
        }

    class FakeClient:
        def __init__(self, api_key):
            pass
        search = staticmethod(fake_search)

    captured = {}

    def fake_rerank(query, docs, top_k=10):
        captured["docs"] = list(docs)
        captured["top_k"] = top_k
        return list(reversed(docs))[:top_k]

    monkeypatch.setattr(research_agent, "VECTOR_STORE_ENABLED", False)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(research_agent, "TavilyClient", FakeClient)
    monkeypatch.setitem(sys.modules, "vector_store", MagicMock(rerank_documents=fake_rerank))

    data = research_agent.research_web("test query 7")

    assert captured["top_k"] == 2
    assert len(captured["docs"]) == 3
    # Reranked output replaces fresh results, capped at top_k
    assert len(data) == 2
    assert all(d["url"].startswith("https://ex") for d in data)
