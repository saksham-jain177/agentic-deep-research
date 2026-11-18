"""Tests for the MCP stdio server (mcp_server.py).

The server wraps the existing streaming pipeline in the Model Context
Protocol so an AI agent can run a research job. The tests that matter most
here are the ones for the failure modes that make an MCP server useless in
practice: a corrupted frame stream (pipeline output leaking into the JSON-RPC
channel) and a crashed server (one bad tool call killing the loop). All tests
run offline: the mock pipeline needs no keys and the live path is only
exercised up to its cost guard.
"""

import io
import json
import os
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cost_guard  # noqa: E402
import mcp_server  # noqa: E402
import stream_cli  # noqa: E402


def _frames(stdout_text: str) -> list:
    """Parse every line of captured stdout as a JSON-RPC frame.

    A single non-JSON line here is the corruption the server exists to
    prevent, so the parse is deliberately strict.
    """
    frames = []
    for line in stdout_text.splitlines():
        if not line.strip():
            continue
        frames.append(json.loads(line))  # raises on corruption - that is the point
    return frames


def _run_session(messages: list, monkeypatch=None) -> list:
    """Feed messages to serve(), return the response frames."""
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    stdout = io.StringIO()
    mcp_server.serve(stdin=stdin, stdout=stdout)
    return _frames(stdout.getvalue())


def _init_and_list() -> list:
    return _run_session([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ])


class TestHandshake:
    def test_initialize_returns_protocol_and_server_info(self):
        frames = _init_and_list()
        result = frames[0]["result"]
        assert result["protocolVersion"] == mcp_server.PROTOCOL_VERSION
        assert result["serverInfo"]["name"] == "agentic-deep-research"
        assert "tools" in result["capabilities"]

    def test_tools_list_exposes_both_tools_with_schemas(self):
        frames = _init_and_list()
        tools = {t["name"]: t for t in frames[1]["result"]["tools"]}
        assert set(tools) == {"deep_research", "read_report"}
        assert tools["deep_research"]["inputSchema"]["required"] == ["query"]
        assert "mock" in tools["deep_research"]["inputSchema"]["properties"]

    def test_ping_and_notifications_get_no_spurious_frames(self):
        frames = _run_session([
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ])
        assert len(frames) == 1  # only the ping answer; notifications stay silent


class TestDeepResearchTool:
    def test_mock_run_returns_full_report(self):
        frames = _run_session([
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
                "name": "deep_research",
                "arguments": {"query": "wheat yields and warming", "mock": True}},
            },
        ])
        result = frames[0]["result"]
        assert "isError" not in result
        text = result["content"][0]["text"]
        assert "wheat" in text.lower()
        assert "## References" in text  # the artifact, not a summary of it
        assert "mode: mock" in text

    def test_mock_run_saves_report_into_results_sandbox(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_RESULTS_DIR", str(tmp_path))
        frames = _run_session([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "deep_research",
                "arguments": {"query": "q", "mock": True, "out": "report.md"}},
            },
        ])
        assert "saved report" in frames[0]["result"]["content"][0]["text"]
        assert (tmp_path / "report.md").is_file()

    def test_out_is_basenames_only_no_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_RESULTS_DIR", str(tmp_path))
        frames = _run_session([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "deep_research",
                "arguments": {"query": "q", "mock": True,
                              "out": "../evil.md"}},
            },
        ])
        text = frames[0]["result"]["content"][0]["text"]
        assert "saved report" in text
        # The file landed inside the sandbox under its basename, not outside it.
        assert (tmp_path / "evil.md").is_file()
        assert not (tmp_path.parent / "evil.md").is_file()

    def test_blank_query_is_a_tool_error_not_a_crash(self):
        frames = _run_session([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "deep_research", "arguments": {"query": "   "}},
            },
        ])
        assert frames[0]["result"]["isError"] is True

    def test_live_path_is_gated_by_the_cost_guard(self):
        # The web app refuses over-cap runs before any API call; the MCP live
        # path must refuse identically. research_web must never be reached.
        with mock.patch.object(cost_guard, "assert_within_cap",
                               side_effect=cost_guard.CostCapExceeded(
                                   1.0, {"cost_usd": {"max": 5.0}})) as guard, \
             mock.patch("research_agent.research_web") as research_web:
            frames = _run_session([
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                    "name": "deep_research",
                    "arguments": {"query": "q", "mock": False}},
                },
            ])
        guard.assert_called_once()
        research_web.assert_not_called()
        result = frames[0]["result"]
        assert result["isError"] is True
        assert "run refused before any API call" in result["content"][0]["text"]


class TestReadReportTool:
    def test_reads_a_saved_report(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_RESULTS_DIR", str(tmp_path))
        (tmp_path / "report.md").write_text("# Report\n\nbody", encoding="utf-8")
        frames = _run_session([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "read_report", "arguments": {"path": "report.md"}},
            },
        ])
        text = frames[0]["result"]["content"][0]["text"]
        assert text.startswith("# Report")

    def test_traversal_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_RESULTS_DIR", str(tmp_path))
        secret = tmp_path.parent / "secrets.txt"
        secret.write_text("token", encoding="utf-8")
        for evil in ("../secrets.txt", "..\\secrets.txt", "sub/../../secrets.txt"):
            frames = _run_session([
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                    "name": "read_report", "arguments": {"path": evil}},
                },
            ])
            assert frames[0]["result"]["isError"] is True, evil

    def test_non_markdown_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_RESULTS_DIR", str(tmp_path))
        (tmp_path / "data.csv").write_text("a,b", encoding="utf-8")
        frames = _run_session([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "read_report", "arguments": {"path": "data.csv"}},
            },
        ])
        assert frames[0]["result"]["isError"] is True

    def test_missing_report_is_a_clean_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_RESULTS_DIR", str(tmp_path))
        frames = _run_session([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "read_report", "arguments": {"path": "nope.md"}},
            },
        ])
        assert frames[0]["result"]["isError"] is True


class TestProtocolRobustness:
    def test_unknown_tool_is_isError(self):
        frames = _run_session([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "delete_everything", "arguments": {}}},
        ])
        assert frames[0]["result"]["isError"] is True
        assert "unknown tool" in frames[0]["result"]["content"][0]["text"]

    def test_malformed_json_line_gets_parse_error_and_server_survives(self):
        stdin = io.StringIO('{not json}\n' + json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")
        stdout = io.StringIO()
        mcp_server.serve(stdin=stdin, stdout=stdout)
        frames = _frames(stdout.getvalue())
        assert frames[0]["error"]["code"] == -32700
        assert frames[1]["result"] == {}  # the ping still answered

    def test_unknown_method_is_method_not_found(self):
        frames = _run_session([
            {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
        ])
        assert frames[0]["error"]["code"] == -32601

    def test_pipeline_prints_cannot_corrupt_the_frame_stream(self):
        # The failure mode that makes an MCP server useless: tool execution
        # writing to sys.stdout mid-protocol. The generator below prints the
        # way a chatty dependency would; every frame on stdout must still
        # parse, and the noise must land on stderr instead.
        def noisy_stream(deep, words):
            print("CHATTY-DEPENDENT-NOISE going to stdout")
            yield from real_mock_stream(deep, words)

        real_mock_stream = stream_cli._mock_stream  # captured BEFORE patching

        stderr = io.StringIO()
        stdin = io.StringIO(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "deep_research",
                       "arguments": {"query": "q", "mock": True}}}) + "\n")
        stdout = io.StringIO()
        with mock.patch.object(stream_cli, "_mock_stream", noisy_stream), \
             mock.patch("sys.stderr", stderr):
            mcp_server.serve(stdin=stdin, stdout=stdout)

        frames = _frames(stdout.getvalue())  # raises if the stream is corrupted
        assert "CHATTY-DEPENDENT-NOISE" not in stdout.getvalue()
        assert "CHATTY-DEPENDENT-NOISE" in stderr.getvalue()
        assert "## References" in frames[0]["result"]["content"][0]["text"]

    def test_full_agent_session_end_to_end(self):
        # What a real MCP client does: initialize, announce, list, call, ping.
        frames = _run_session([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "deep_research",
                "arguments": {"query": "test query", "mock": True}}},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        ])
        assert [f.get("id") for f in frames] == [1, 2, 3, 4]
        assert frames[1]["result"]["tools"][0]["name"] == "deep_research"
        assert "## References" in frames[2]["result"]["content"][0]["text"]
