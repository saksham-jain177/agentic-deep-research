"""Tests for draft_agent.format_citation (dict -> Source mapping fix)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from draft_agent import format_citation


class TestFormatCitation:
    def test_app_preview_dict_with_string_author_and_year(self):
        """The app.py style-preview dict (author str, date '2024') must format."""
        source = {
            "title": "Impact of LLMs on Deep Research",
            "url": "https://arxiv.org/abs/2401.0001",
            "author": "Antigravity AI",
            "publisher": "Research DeepMind",
            "date": "2024",
        }
        result = format_citation(source, "APA")
        assert "Available at:" not in result  # no silent-fallback marker
        assert "Impact of LLMs on Deep Research." in result
        assert "Antigravity AI" in result
        assert "(2024)." in result
        assert "Research DeepMind." in result
        assert "Retrieved from https://arxiv.org/abs/2401.0001" in result

    def test_research_agent_minimal_dict(self):
        """Sources from research_web carry only title/content/url."""
        source = {
            "title": "Quantum Computing Advances",
            "content": "some scraped body text",
            "url": "https://research.edu/quantum",
        }
        result = format_citation(source, "APA")
        assert "Available at:" not in result
        assert "Quantum Computing Advances." in result
        assert "(n.d.)." in result
        assert "Retrieved from https://research.edu/quantum" in result

    def test_conftest_style_dict_with_authors_list_and_iso_date(self):
        """authors list + ISO publication_date map onto Source correctly."""
        source = {
            "title": "Climate Change Effects on Agriculture",
            "url": "https://example.com/climate",
            "authors": ["John Smith", "Jane Doe"],
            "publication_date": "2024-01-15",
        }
        result = format_citation(source, "APA")
        assert "John Smith & Jane Doe" in result
        assert "(2024)." in result
        assert "Climate Change Effects on Agriculture." in result

    def test_styles_route_to_correct_formatters(self):
        source = {
            "title": "Some Study",
            "url": "https://example.com/s",
            "author": "J. Doe",
            "date": "2023-06-20",
        }
        mla = format_citation(source, "MLA")
        ieee = format_citation(source, "IEEE")
        bibtex = format_citation(source, "BibTeX")
        assert '"Some Study."' in mla
        assert "[Online]. Available:" in ieee
        assert "@misc{" in bibtex
        for out in (mla, ieee, bibtex):
            assert "Available at:" not in out

    def test_unparseable_date_degrades_to_nd_without_crash(self):
        source = {
            "title": "Undated Page",
            "url": "https://example.com/x",
            "date": "sometime soon?",
        }
        result = format_citation(source, "APA")
        assert "Undated Page." in result
        assert "(n.d.)." in result
