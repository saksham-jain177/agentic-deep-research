"""Tests for the one-command CLI surface: `python stream_cli.py "query" --out report.md`.

The CLI is the entry point a reviewer or a stranger actually types, and the
Markdown file it writes is what they judge the project on, so these tests pin
the two properties that matter: the file contains the verification verdicts
(the thing that distinguishes this report from a generic one), and the free
`--mock` demo cannot spend the caller's credits or leak their key.
"""

import os

import pytest

import stream_cli
from draft_agent import (
    STREAM_EVENT_CONTRADICTIONS,
    STREAM_EVENT_DONE,
    STREAM_EVENT_SECTION,
)


def _fake_stream():
    yield {
        "event": STREAM_EVENT_SECTION,
        "index": 0,
        "title": "Key Findings",
        "content": "Wheat yields fall 6% per degree [1].\n\nIrrigation offsets part of it [2].",
        "verification": {
            "section": "Key Findings",
            "verdict": "partially_supported",
            "unsupported_claims": ["Irrigation offsets part of it"],
            "citations_coverage": 1.0,
            "notes": "second paragraph not established by [2]",
        },
    }
    yield {"event": STREAM_EVENT_CONTRADICTIONS, "items": [{
        "severity": "high",
        "topic": "yield decline",
        "source_id_a": 1,
        "claim_a": "yields fall 6% per degree",
        "source_id_b": 2,
        "claim_b": "yields are stable",
    }]}
    yield {
        "event": STREAM_EVENT_DONE,
        "sections": [],
        "references": ["[1] (n.d.). Climate report. https://example.com/climate",
                       "[2] (n.d.). Agri study. https://example.org/agri"],
        "metadata": {"model": "openrouter/test", "language": "english",
                     "writing_style": "academic", "citation_format": "APA"},
    }


class TestReportMarkdown:
    def test_sections_verdicts_and_references_are_rendered(self):
        report = stream_cli._consume(_fake_stream())
        text = stream_cli.report_markdown("wheat yields", report["sections"],
                                         report["contradictions"], report["references"],
                                         report["metadata"])

        assert text.startswith("# wheat yields")
        assert "## Key Findings" in text
        assert "partially_supported" in text
        assert "unsupported: Irrigation offsets part of it" in text
        assert "citation coverage 100%" in text
        assert "## Conflicting Evidence" in text
        assert "**[HIGH]** yield decline" in text
        assert "## References" in text
        assert "`openrouter/test`" in text

    def test_absent_sections_are_omitted_not_printed_empty(self):
        text = stream_cli.report_markdown("q", [], [], [], {})
        assert text == "# q\n"

    def test_missing_verdict_renders_the_section_alone(self):
        sections = [{"title": "Abstract", "content": "No judge configured.",
                     "verification": None}]
        text = stream_cli.report_markdown("q", sections, [], [], {})
        assert "## Abstract" in text
        assert "Verification" not in text


class TestConsume:
    def test_it_collects_every_part_of_the_report(self):
        report = stream_cli._consume(_fake_stream())
        assert report["events"] == 3
        assert report["sections"][0]["title"] == "Key Findings"
        assert report["contradictions"][0]["severity"] == "high"
        assert len(report["references"]) == 2
        assert report["metadata"]["citation_format"] == "APA"


class TestMockDemoIsFree:
    """`--mock` is advertised as costing nothing; a caller with real keys must
    not pay for it and must not have their key handed to the judge."""

    def test_real_key_is_not_in_the_environment_while_mocking(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-user-real-key")
        seen = []
        original = stream_cli.draft_agent.stream_draft_answer

        def spy(*args, **kwargs):
            seen.append(os.getenv("OPENROUTER_API_KEY"))
            return iter([])

        monkeypatch.setattr(stream_cli.draft_agent, "stream_draft_answer", spy)
        list(stream_cli._mock_stream(False, 100))

        assert seen == ["sk-mock-key-for-demo"]

    def test_environment_is_restored_after_the_demo(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-user-real-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        list(stream_cli._mock_stream(False, 100))
        assert os.getenv("OPENROUTER_API_KEY") == "sk-user-real-key"

    def test_mock_runs_keyless_and_produces_a_report(self, monkeypatch, tmp_path):
        for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
            monkeypatch.delenv(name, raising=False)
        target = tmp_path / "report.md"
        monkeypatch.setattr(
            "sys.argv", ["stream_cli.py", "wheat yields", "--mock", "--out", str(target)]
        )
        assert stream_cli.main() == 0
        text = target.read_text(encoding="utf-8")
        assert text.startswith("# wheat yields")
        assert "## Key Findings" in text
        assert "https://example.com/climate" in text


class TestOutFile:
    def test_writing_to_an_unwritable_path_fails_loudly(self, monkeypatch, capsys):
        bad_path = os.path.join("Z:\\", "definitely", "absent", "report.md")
        monkeypatch.setattr(
            "sys.argv", ["stream_cli.py", "--mock", "--out", bad_path]
        )
        assert stream_cli.main() == 1
        assert "Could not write" in capsys.readouterr().err
