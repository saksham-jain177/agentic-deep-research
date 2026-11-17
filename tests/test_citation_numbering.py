"""Regression tests for bibliography numbering and dangling inline citations.

Before the fix, inline ``[n]`` markers addressed the evidence table while the
bibliography was a compacted list of only the cited sources, and every entry
printed ``[1]`` because the formatter was constructed one source at a time. A
body citing ``[17]`` against five shipped references was therefore normal.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import draft_agent
from eval.citations import parse_report

SOURCES = [
    {"title": f"Source {i}", "url": f"https://example.com/{i}", "content": "x"}
    for i in range(1, 9)
]


# --- format_citation -------------------------------------------------------

def test_every_reference_does_not_print_one():
    numbers = [
        re.match(r"^\[(\d+)\]", draft_agent.format_citation(SOURCES[i], "APA", i + 1)).group(1)
        for i in range(4)
    ]
    assert numbers == ["1", "2", "3", "4"]


def test_number_none_keeps_legacy_output():
    assert draft_agent.format_citation(SOURCES[0], "APA").startswith("[1] ")


def test_bibtex_gets_no_marker():
    entry = draft_agent.format_citation(SOURCES[0], "BibTeX", 3)
    assert not entry.startswith("[3]")


def test_fallback_path_is_still_numbered(monkeypatch):
    monkeypatch.setitem(sys.modules, "citation_formatter", None)
    assert draft_agent.format_citation(SOURCES[0], "APA", 7).startswith("[7] ")


# --- registry / renumbering ------------------------------------------------

def test_registry_assigns_first_cited_order():
    reg = draft_agent._CitationRegistry()
    assert reg.assign({5, 3}) == {3: 1, 5: 2}
    assert reg.assign({5}) == {5: 2}
    assert reg.order == [3, 5]


def test_renumber_is_single_pass():
    # Naive sequential str.replace would turn [2]->[1] then [1]->[3] into [3][3].
    mapping = {1: 3, 2: 1}
    assert draft_agent._renumber_citations("a [1] b [2] c", mapping) == "a [3] b [1] c"


def test_renumber_ignores_unknown_and_non_numeric():
    assert draft_agent._renumber_citations("see [9] and [abc]", {2: 1}) == "see [9] and [abc]"


# --- draft_answer end to end -----------------------------------------------

class ScriptedLLM:
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.calls = 0

    def invoke(self, messages):
        body = self.bodies[min(self.calls, len(self.bodies) - 1)]
        self.calls += 1

        class Resp:
            pass

        Resp.content = body
        return Resp()


@pytest.fixture
def scripted(monkeypatch):
    import verification_node
    import contradiction_detector

    def _install(bodies):
        monkeypatch.setattr(draft_agent, "_get_llm", lambda: ScriptedLLM(bodies))
        monkeypatch.setattr(draft_agent.time, "sleep", lambda s: None)
        monkeypatch.setattr(verification_node, "verify_report", lambda *a, **k: [])
        monkeypatch.setattr(verification_node, "verify_section", lambda *a, **k: None)
        monkeypatch.setattr(contradiction_detector, "detect_contradictions", lambda *a, **k: [])
    return _install


def _markers(text):
    return [int(n) for n in re.findall(r"\[(\d+)\]", text)]


def test_inline_markers_resolve_against_bibliography(scripted):
    # Two sections, cited out of evidence-table order and overlapping.
    scripted(["Warming trend measured [5]. Sea levels rose [3].",
              "Repeat of five [5] plus a new one [7]."])
    result = json.loads(draft_agent.draft_answer(data=SOURCES, deep_research=False))

    refs = result["references"]
    assert [re.match(r"^\[(\d+)\]", r).group(1) for r in refs] == [str(i) for i in range(1, len(refs) + 1)]
    # 3 and 5 from the first section, then 7 - first-cited order, deduplicated.
    assert len(refs) == 3
    assert "Source 3" in refs[0] and "Source 5" in refs[1] and "Source 7" in refs[2]

    body = "\n".join(s["content"] for s in result["sections"] if s["title"] != "Verification Summary")
    assert max(_markers(body)) <= len(refs)


def test_report_has_no_dangling_citations(scripted):
    scripted(["Warming trend measured [5]. Sea levels rose [3].",
              "Repeat of five [5] plus a new one [7]."])
    result = json.loads(draft_agent.draft_answer(data=SOURCES, deep_research=False))

    sections = [s for s in result["sections"] if s["title"] != "Verification Summary"]
    report = "\n\n".join(f"## {s['title']}\n{s['content']}" for s in sections)
    report += "\n\n## References\n" + "\n".join(result["references"])

    parsed = parse_report(report)
    assert parsed.dangling == []
    assert len(parsed.references) == 3


def test_streaming_matches_non_streaming_numbering(scripted):
    bodies = ["First [4].", "Second [4] then [2]."]
    scripted(bodies)
    streamed = draft_agent.collect_streamed_report(
        draft_agent.stream_draft_answer(data=SOURCES, deep_research=False)
    )
    assert [re.match(r"^\[(\d+)\]", r).group(1) for r in streamed["references"]] == ["1", "2"]
    body = "\n".join(s["content"] for s in streamed["sections"] if s["title"] != "Verification Summary")
    assert set(_markers(body)) <= {1, 2}


# --- rendering -------------------------------------------------------------

def test_reference_line_does_not_double_number():
    app = pytest.importorskip("app")
    assert app._reference_line(2, "[2] Doe (2024). T.") == "[2] Doe (2024). T."
    assert app._reference_line(2, "@article{x,") == "2. @article{x,"
