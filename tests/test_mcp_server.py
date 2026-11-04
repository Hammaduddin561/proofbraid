"""Tests for the stdio MCP server (mocked stdio, no network, no keys)."""

import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from proofbraid import mcp_server, propose as propose_mod  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample_repo"


def _rpc(*messages):
    """Feed raw lines (str) or messages (dict) through serve()."""
    lines = [m if isinstance(m, str) else json.dumps(m) for m in messages]
    out = io.StringIO()
    mcp_server.serve(io.StringIO("\n".join(lines) + "\n"), out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def _call(name, arguments, msg_id=1):
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


class TestMcpServer(unittest.TestCase):
    def test_initialize_returns_server_info(self):
        [resp] = _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05"}},
                      {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "proofbraid")
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_tools_list_returns_all_five(self):
        [resp] = _rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = resp["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {
            "index_repository", "get_graph", "verify_repo",
            "propose_change", "list_hypotheses"})
        self.assertTrue(all(t["description"] and t["inputSchema"] for t in tools))

    def test_verify_repo_on_sample_reports_fail(self):
        [resp] = _rpc(_call("verify_repo", {"repo_path": str(SAMPLE)}))
        result = resp["result"]
        self.assertTrue(result["isError"])
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["result"], "fail")
        self.assertTrue(any("tests failed" in f for f in payload["failures"]))

    def test_propose_change_runs_propose_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            shutil.copytree(SAMPLE, repo,
                            ignore=shutil.ignore_patterns(".proofbraid"))
            original = (repo / "calc.py").read_text()
            fixed = original.replace(
                "return a + b + 1  # BUG: off by one", "return a + b")
            with mock.patch.object(
                    propose_mod.providers, "chat_json",
                    return_value={"files": [{"path": "calc.py",
                                             "content": fixed}]}) as chat, \
                    mock.patch.object(propose_mod.aux_mod,
                                      "summarize_hypothesis", return_value=None):
                [resp] = _rpc(_call("propose_change", {
                    "repo_path": str(repo), "task": "fix add",
                    "provider": "baseten"}))
                [hyps] = _rpc(_call("list_hypotheses", {"repo_path": str(repo)}))
            payload = json.loads(resp["result"]["content"][0]["text"])
            self.assertFalse(resp["result"]["isError"])
            self.assertTrue(payload["ok"])
            self.assertFalse(payload["applied"])
            self.assertEqual(chat.call_args.args[0], "baseten")
            self.assertEqual((repo / "calc.py").read_text(), original)
            listed = json.loads(hyps["result"]["content"][0]["text"])
            self.assertEqual(listed["hypotheses"][0]["id"], payload["hypothesis"])

    def test_malformed_json_gets_parse_error_and_server_continues(self):
        responses = _rpc("{not json", {"jsonrpc": "2.0", "id": 3,
                                       "method": "nope"})
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertIsNone(responses[0]["id"])
        self.assertEqual(responses[1]["error"]["code"], -32601)

    def test_unknown_tool_and_missing_args_are_clean_errors(self):
        unknown, missing = _rpc(_call("rm_rf", {}), _call("verify_repo", {}, 2))
        self.assertEqual(unknown["error"]["code"], -32602)
        self.assertIn("repo_path", missing["error"]["message"])


if __name__ == "__main__":
    unittest.main()
