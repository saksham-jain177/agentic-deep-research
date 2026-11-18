"""CLI for the research pipeline: stream a report, optionally save it as Markdown.

Runs the streaming draft flow, prints each section to stdout as it arrives,
along with its per-section verification verdict. Uses the same env vars as the
rest of the pipeline (OPENROUTER_API_KEY / OPENAI_API_KEY, TAVILY_API_KEY).

Usage:
    python stream_cli.py "your research query" [--deep] [--words 1000] [--out report.md]
    python stream_cli.py --mock            # offline demo, no API keys

--out writes the same content stdout showed as a Markdown file: sections with
their verification verdict, the conflicting-evidence table, and the reference
list. It is deliberately not a re-implementation of the exporters in
app.py -- those render PDF/DOCX from the non-streaming path; this one keeps the
streaming path's per-section verdicts, which is what makes the file reviewable.
"""

import argparse
import os
import sys

import draft_agent


def _print_event(event: dict) -> None:
    kind = event.get("event")
    if kind == draft_agent.STREAM_EVENT_SECTION:
        print(f"\n=== [{event['index']}] {event['title']} ===")
        print(event["content"])
        verdict = event.get("verification")
        if verdict:
            print(f"\n[verification] {verdict['section']}: "
                  f"{verdict['verdict']} "
                  f"(citation coverage: {verdict['citations_coverage']:.0%})")
        sys.stdout.flush()
    elif kind == draft_agent.STREAM_EVENT_SECTION_ERROR:
        print(f"\n!! section '{event['title']}' failed: {event['error']}",
              file=sys.stderr)
        sys.stderr.flush()
    elif kind == draft_agent.STREAM_EVENT_CONTRADICTIONS:
        print("\n--- Conflicting Evidence ---")
        for c in event["items"]:
            print(f"  - [{c.get('severity', '?').upper()}] {c.get('topic', '')}")
            print(f'    source [{c["source_id_a"]}]: "{c["claim_a"]}"')
            print(f'    source [{c["source_id_b"]}]: "{c["claim_b"]}"')
        sys.stdout.flush()
    elif kind == draft_agent.STREAM_EVENT_DONE:
        if event.get("error"):
            print(f"\nstream aborted: {event['error']}", file=sys.stderr)
            return
        print("\n--- References ---")
        for ref in event.get("references", []):
            print(f"* {ref}")
        meta = event.get("metadata", {})
        print(f"\n(model={meta.get('model')}, language={meta.get('language')}, "
              f"style={meta.get('writing_style')})")


def report_markdown(query: str, sections: list, contradictions: list,
                    references: list, metadata: dict) -> str:
    """Render a consumed stream as Markdown, keeping the verification verdicts."""
    lines = [f"# {query}", ""]
    for section in sections:
        lines.append(f"## {section['title']}")
        lines.append(section["content"].strip())
        verdict = section.get("verification")
        if verdict:
            lines.append("")
            lines.append(f"> **Verification:** {verdict['verdict']} "
                         f"(citation coverage {verdict['citations_coverage']:.0%})")
            for claim in verdict.get("unsupported_claims") or []:
                lines.append(f"> - unsupported: {claim}")
            note = (verdict.get("notes") or "").strip()
            if note:
                lines.append(f"> - judge notes: {note}")
        lines.append("")
    if contradictions:
        lines.append("## Conflicting Evidence")
        lines.append("")
        for item in contradictions:
            severity = str(item.get("severity", "?")).upper()
            lines.append(f"- **[{severity}]** {item.get('topic', '')}")
            lines.append(f"  - source [{item.get('source_id_a')}]: "
                         f"\"{item.get('claim_a', '')}\"")
            lines.append(f"  - source [{item.get('source_id_b')}]: "
                         f"\"{item.get('claim_b', '')}\"")
        lines.append("")
    if references:
        lines.append("## References")
        lines.append("")
        for ref in references:
            lines.append(f"- {ref}")
        lines.append("")
    if metadata:
        lines.append("---")
        lines.append("")
        lines.append(f"- model: `{metadata.get('model')}`")
        lines.append(f"- language: `{metadata.get('language')}`")
        lines.append(f"- writing style: `{metadata.get('writing_style')}`")
        lines.append(f"- citation format: `{metadata.get('citation_format')}`")
        lines.append("")
    return "\n".join(lines)


def _consume(stream) -> dict:
    """Print each event and collect the canonical report dict.

    Delegates collection to ``draft_agent.collect_streamed_report`` (the
    single construction site of the report contract) with this module's
    printer as the per-event callback, so live output and collection stay in
    one pass and the CLI emits the same versioned shape as the MCP server.
    """
    return draft_agent.collect_streamed_report(stream, on_event=_print_event)


def _mock_stream(deep: bool, words: int):
    """Stub the streaming path so the protocol can be seen with no key and no spend.

    All three LLM entry points in this path get patched -- drafting, the judge,
    and the contradiction pass -- and the real key is cleared for the duration.
    A demo the README calls "no API keys" must not quietly bill the caller who
    happens to have a working .env.
    """
    from contextlib import ExitStack
    from unittest.mock import patch

    class Resp:
        content = ("Wheat yields drop 6% per degree of warming [1].\n\n"
                   "Adaptation relies on drought-resistant cultivars [2].")

    class StubLLM:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            return Resp()

    saved = {name: os.environ.get(name)
             for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL")}
    os.environ["OPENROUTER_API_KEY"] = "sk-mock-key-for-demo"
    os.environ.pop("OPENAI_API_KEY", None)
    try:
        with ExitStack() as stack:
            for target in ("draft_agent.ChatOpenAI", "verification_node.ChatOpenAI",
                           "contradiction_detector.ChatOpenAI",
                           "evidence_extractor.ChatOpenAI"):
                stack.enter_context(patch(target, StubLLM))
            yield from draft_agent.stream_draft_answer(
                [{"title": "Climate report", "url": "https://example.com/climate",
                  "content": "Global wheat yields fall by 6% per degree of warming."},
                 {"title": "Agri study", "url": "https://example.org/agri",
                  "content": "Farmers adapt via drought-resistant cultivars."}],
                deep_research=deep, target_word_count=words)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main() -> int:
    parser = argparse.ArgumentParser(description="Stream a research report to stdout")
    parser.add_argument("query", nargs="?", default="climate change and agriculture",
                        help="Research query")
    parser.add_argument("--deep", action="store_true", help="Deep research mode (6 sections)")
    parser.add_argument("--words", type=int, default=1000, help="Target word count")
    parser.add_argument("--out", metavar="PATH",
                        help="Write the report as Markdown to PATH")
    parser.add_argument("--mock", action="store_true",
                        help="Offline demo with a stubbed LLM (no API keys)")
    args = parser.parse_args()

    if args.mock:
        report = _consume(_mock_stream(args.deep, args.words))
    else:
        # Same gate the web app and the MCP server apply (ROADMAP item 10):
        # refuse the run before any API call when MAX_RUN_COST_USD is set and
        # the worst-case estimate exceeds it. --mock spends nothing and is
        # never gated.
        import cost_guard

        try:
            cost_guard.assert_within_cap(
                args.query, deep_research=args.deep, target_word_count=args.words
            )
        except cost_guard.CostCapExceeded as exc:
            print(f"Run refused: {exc}", file=sys.stderr)
            return 2
        try:
            import research_agent
            results = research_agent.research_web(args.query, deep_research=args.deep)
        except Exception as e:
            print(f"Gathering sources failed ({type(e).__name__}: {e}); "
                  f"use --mock for an offline demo.", file=sys.stderr)
            return 1
        report = _consume(draft_agent.stream_draft_answer(
            results, deep_research=args.deep, target_word_count=args.words))

    if args.out:
        text = report_markdown(args.query, report["sections"], report["contradictions"],
                               report["references"], report["metadata"])
        try:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as e:
            print(f"Could not write {args.out}: {e}", file=sys.stderr)
            return 1
        print(f"\nSaved report to {args.out}", file=sys.stderr)

    return 0 if report["events"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
