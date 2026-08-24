"""CLI demo for streaming report generation.

Runs the streaming draft flow and prints each section to stdout as it
arrives, along with its per-section verification verdict. Uses the same
env vars as the rest of the pipeline (OPENROUTER_API_KEY / OPENAI_API_KEY).

Usage:
    python stream_cli.py "your research query" [--deep] [--words 1000]

Offline demo (no API keys): pass --mock to see the event protocol with a
stubbed LLM.
"""

import argparse
import json
import sys

from typing import List, Dict, Any

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Stream a research report to stdout")
    parser.add_argument("query", nargs="?", default="climate change and agriculture",
                        help="Research query")
    parser.add_argument("--deep", action="store_true", help="Deep research mode (6 sections)")
    parser.add_argument("--words", type=int, default=1000, help="Target word count")
    parser.add_argument("--mock", action="store_true",
                        help="Offline demo with a stubbed LLM (no API keys)")
    args = parser.parse_args()

    if args.mock:
        # Reuse the mock path but through the real generator.
        import os
        os.environ.setdefault("OPENROUTER_API_KEY", "sk-mock-key-for-demo")

        from unittest.mock import patch

        class Resp:
            content = ("Wheat yields drop 6% per degree of warming [1].\n\n"
                       "Adaptation relies on drought-resistant cultivars [2].")

        data = [
            {"title": "Climate report", "url": "https://example.com/climate",
             "content": "Global wheat yields fall by 6% per degree of warming."},
            {"title": "Agri study", "url": "https://example.org/agri",
             "content": "Farmers adapt via drought-resistant cultivars."},
        ]
        with patch("draft_agent.ChatOpenAI", lambda **kw: type("L", (), {"invoke": lambda self, m: Resp()})()):
            for event in draft_agent.stream_draft_answer(data, deep_research=args.deep,
                                                         target_word_count=args.words):
                _print_event(event)
        return 0

    # Real path: gather sources via the existing pipeline entry point is out
    # of scope here; this CLI demonstrates the streaming draft over provided
    # or trivially-built data using the research agent when available.
    try:
        import research_agent
        results = research_agent.research_web(args.query, deep_research=args.deep)
    except Exception as e:
        print(f"Gathering sources failed ({type(e).__name__}: {e}); "
              f"use --mock for an offline demo.", file=sys.stderr)
        return 1

    events_printed = 0
    for event in draft_agent.stream_draft_answer(
        results, deep_research=args.deep, target_word_count=args.words
    ):
        _print_event(event)
        events_printed += 1
    return 0 if events_printed else 1


if __name__ == "__main__":
    raise SystemExit(main())
