"""MCP stdio server: run the research pipeline from any AI agent.

Exposes the same streaming pipeline the CLI and the Streamlit app drive as
Model Context Protocol tools, so an MCP client (Claude Desktop, Cursor, any
agent speaking the protocol) can run a research job and read the report back.

Zero new dependencies: the stdio transport is newline-delimited JSON-RPC 2.0,
implemented below in ~150 lines. Adding the official SDK would pin a package
against the deliberately lean requirements.txt for no functional gain.

Protocol discipline (the part that actually matters): MCP frames are JSON
lines on stdout. Anything the pipeline, LangChain, or a stray print writes to
sys.stdout during a tool call would corrupt the stream, so the real stdout
handle is captured at startup, every frame is written to that handle, and
tool execution runs with sys.stdout redirected to stderr. A corrupted frame
stream is the failure mode that makes an MCP server useless, so
tests/test_mcp_server.py pins it.

Tools:
    deep_research(query, deep=False, target_word_count=1000, mock=False, out=None)
        Live mode gathers sources (Tavily) and streams the draft with
        per-section verification, exactly like `python stream_cli.py "q"`.
        mock=True runs the stubbed offline pipeline -- no API keys, no spend --
        which is how an agent should smoke-test the integration before
        spending quota. The live path is gated by cost_guard.assert_within_cap
        (MAX_RUN_COST_USD), the same gate the web app's run_research applies.
    read_report(path)
        Returns a saved Markdown report. Sandboxed to the results directory
        (override with MCP_RESULTS_DIR for tests): no traversal, .md only.

Usage:
    python mcp_server.py            # speak MCP on stdio until EOF

Claude Desktop / Cursor config:
    {
      "mcpServers": {
        "agentic-deep-research": {
          "command": "python",
          "args": ["/path/to/agentic-deep-research/mcp_server.py"]
        }
      }
    }
"""

import json
import os
import sys
import traceback

import draft_agent
import stream_cli

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agentic-deep-research"
SERVER_VERSION = "0.5.0"
MAX_REPORT_CHARS = 50_000

# The one true stdout. MCP frames go here and nowhere else.
_REAL_STDOUT = None


def _results_dir() -> str:
    return os.environ.get("MCP_RESULTS_DIR") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "results"
    )


def _write_frame(payload: dict) -> None:
    """Write one JSON-RPC frame to the real stdout.

    sys.stdout may be redirected (tool execution does exactly that), so frames
    are written to the handle captured at startup and flushed immediately.
    """
    handle = _REAL_STDOUT if _REAL_STDOUT is not None else sys.__stdout__
    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    handle.flush()


class ToolError(Exception):
    """A tool failed in a way the caller should see as isError content."""


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

def _collect(stream) -> dict:
    """Consume stream events into the canonical report dict.

    Delegates to ``draft_agent.collect_streamed_report`` - the single
    construction site of the versioned report contract (stream_cli._consume
    delegates to the same function with a printer attached).
    """
    return draft_agent.collect_streamed_report(stream)


def _tool_deep_research(arguments: dict) -> str:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolError("query must be a non-empty string")
    query = query.strip()
    deep = bool(arguments.get("deep", False))
    words = int(arguments.get("target_word_count", 1000))
    mock = bool(arguments.get("mock", False))
    out = arguments.get("out") or None

    if mock:
        report = _collect(stream_cli._mock_stream(deep, words))
    else:
        # Same gate the web app's run_research applies: refuse the run before
        # any API call when MAX_RUN_COST_USD is set and the worst-case
        # estimate exceeds it.
        import cost_guard
        cost_guard.assert_within_cap(query, deep_research=deep, target_word_count=words)
        try:
            import research_agent
            results = research_agent.research_web(query, deep_research=deep)
        except Exception as exc:  # noqa: BLE001 - surfaced as a tool error
            raise ToolError(
                f"Gathering sources failed ({type(exc).__name__}: {exc}); "
                "pass mock=true for an offline demo."
            ) from exc
        report = _collect(draft_agent.stream_draft_answer(
            results, deep_research=deep, target_word_count=words))

    if not report["events"]:
        raise ToolError("The pipeline produced no output for this query.")

    text = stream_cli.report_markdown(
        query, report["sections"], report["contradictions"],
        report["references"], report["metadata"])

    saved = None
    if out:
        # Only ever write inside the results sandbox, basename only.
        target = os.path.join(_results_dir(), os.path.basename(out))
        os.makedirs(_results_dir(), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)
        saved = target

    summary = [
        text,
        "",
        "---",
        f"sections: {len(report['sections'])} | "
        f"contradictions: {len(report['contradictions'])} | "
        f"references: {len(report['references'])} | "
        f"mode: {'mock' if mock else 'live'}",
    ]
    if saved:
        summary.append(f"saved report: {saved}")
    return "\n".join(summary)


def _tool_read_report(arguments: dict) -> str:
    path = arguments.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ToolError("path must be a non-empty string")
    # Sandbox: the agent may read reports this server (or the CLI) wrote into
    # the results directory, and nothing else. Basename + join kills `..`
    # traversal; the suffix check keeps this a report reader, not a file reader.
    target = os.path.join(_results_dir(), os.path.basename(path.strip()))
    if not target.endswith(".md"):
        raise ToolError("only .md reports can be read")
    if not os.path.isfile(target):
        raise ToolError(f"no report named {os.path.basename(path)!r} in "
                        f"{_results_dir()}")
    with open(target, "r", encoding="utf-8") as handle:
        text = handle.read()
    if len(text) > MAX_REPORT_CHARS:
        text = text[:MAX_REPORT_CHARS] + f"\n\n[truncated at {MAX_REPORT_CHARS} chars]"
    return text


TOOLS = {
    "deep_research": {
        "description": (
            "Run the deep-research pipeline on a query and return the full "
            "Markdown report with per-section verification verdicts, "
            "conflicting evidence, and a reference list. mock=true runs the "
            "stubbed offline pipeline (no API keys, no spend) for integration "
            "testing; live mode gathers real web sources and is gated by the "
            "MAX_RUN_COST_USD cost cap."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "The research question."},
                "deep": {"type": "boolean", "default": False,
                         "description": "Deep research mode (6 sections)."},
                "target_word_count": {"type": "integer", "default": 1000,
                                      "description": "Target report length."},
                "mock": {"type": "boolean", "default": False,
                         "description": "Offline stubbed run; no keys, no spend."},
                "out": {"type": "string",
                        "description": "Optional filename (basename only) to "
                                       "save the report under the results "
                                       "directory; read it back with read_report."},
            },
            "required": ["query"],
        },
    },
    "read_report": {
        "description": (
            "Read a Markdown report previously saved into the results "
            "directory (by deep_research out=... or python stream_cli.py "
            "--out). Sandboxed to that directory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Report filename."},
            },
            "required": ["path"],
        },
    },
}

TOOL_HANDLERS = {
    "deep_research": _tool_deep_research,
    "read_report": _tool_read_report,
}


# --------------------------------------------------------------------------
# JSON-RPC / MCP plumbing
# --------------------------------------------------------------------------

def _rpc_error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def _handle_request(message: dict) -> dict:
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "tools": [{"name": name, "description": spec["description"],
                       "inputSchema": spec["inputSchema"]}
                      for name, spec in TOOLS.items()]}}
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text",
                             "text": f"unknown tool: {name}"}],
                "isError": True}}
        try:
            text = handler(arguments)
        except ToolError as exc:
            text = str(exc)
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": text}], "isError": True}}
        except Exception as exc:  # noqa: BLE001 - the agent sees a clean error
            if os.environ.get("MCP_DEBUG"):
                traceback.print_exc(file=sys.stderr)
            text = f"{type(exc).__name__}: {exc}"
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": text}], "isError": True}}
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "content": [{"type": "text", "text": text}]}}
    if method and not request_id is None:
        return _rpc_error(request_id, -32601, f"method not found: {method}")
    if method:
        # A notification we do not handle (e.g. notifications/initialized,
        # notifications/cancelled): per JSON-RPC, no response, ever.
        return {}
    return _rpc_error(None, -32600, "invalid request: missing method")


def serve(stdin=None, stdout=None) -> int:
    """Read JSON-RPC frames until EOF, dispatch, answer.

    stdin/stdout parameters exist for tests; production uses the real streams.
    """
    global _REAL_STDOUT
    stream_in = stdin if stdin is not None else sys.stdin
    _REAL_STDOUT = stdout if stdout is not None else sys.__stdout__

    # Frame-stream discipline: while the server runs, sys.stdout is NOT the
    # MCP channel. Anything the pipeline, LangChain, or a stray print writes
    # to sys.stdout goes to stderr instead of corrupting the JSON-RPC stream;
    # frames are written to _REAL_STDOUT (captured above) by _write_frame.
    saved_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        for line in stream_in:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                _write_frame(_rpc_error(None, -32700, f"parse error: {exc}"))
                continue
            if not isinstance(message, dict):
                _write_frame(_rpc_error(None, -32600, "invalid request: not an object"))
                continue

            response = {}
            try:
                response = _handle_request(message)
            except Exception as exc:  # noqa: BLE001 - the server must survive bugs
                if os.environ.get("MCP_DEBUG"):
                    traceback.print_exc(file=sys.stderr)
                response = _rpc_error(message.get("id"), -32603,
                                      f"internal error: {type(exc).__name__}: {exc}")
            if response:
                _write_frame(response)
    finally:
        sys.stdout = saved_stdout
    return 0


def main() -> int:
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
