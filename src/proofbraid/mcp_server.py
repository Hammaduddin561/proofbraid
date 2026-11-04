"""Minimal MCP server: Proofbraid's verify before apply loop as tools.

Speaks JSON-RPC 2.0 over stdio, one JSON message per line. Only the
minimal MCP surface is implemented: ``initialize``, ``tools/list`` and
``tools/call``. Every tool calls the existing module functions directly.
Stdout carries JSON-RPC only; anything else printed goes to stderr.
"""

from __future__ import annotations

import contextlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from . import graph as graph_mod
from . import propose as propose_mod
from . import store, verify

PROTOCOL_VERSION = "2024-11-05"
GRAPH_LIMIT = 200


def _repo(args: dict) -> Path:
    return Path(args["repo_path"])


def _index_repository(args: dict) -> dict:
    graph = graph_mod.index_repo(_repo(args))
    graph_mod.save_graph(graph)
    propose_mod._refresh_vectors(graph)
    return {"files_indexed": len(graph.files), "symbols": len(graph.symbols)}


def _get_graph(args: dict) -> dict:
    repo = _repo(args)
    graph = graph_mod.load_graph(repo) or graph_mod.index_repo(repo)
    symbols = [{"name": s.name, "kind": s.kind, "file": s.file,
                "line": s.line, "calls": s.calls}
               for s in graph.symbols[:GRAPH_LIMIT]]
    result = {"symbols": symbols, "total_symbols": len(graph.symbols)}
    if len(graph.symbols) > GRAPH_LIMIT:
        result["note"] = f"truncated to the first {GRAPH_LIMIT} symbols"
    return result


def _verify_repo(args: dict) -> dict:
    failures = verify.verify_repo(_repo(args))
    return {"ok": not failures, "result": "fail" if failures else "pass",
            "failures": failures}


def _propose_change(args: dict) -> dict:
    # run_propose only applies after a verified pass, exactly like the CLI.
    return propose_mod.run_propose(
        _repo(args), args["task"], apply=args.get("apply") is True,
        provider=args.get("provider") or "meta",
    )


def _list_hypotheses(args: dict) -> dict:
    return {"hypotheses": [asdict(h) for h in store.load_hypotheses(_repo(args))]}


def _schema(**props: dict) -> dict:
    required = [name for name, spec in props.items() if spec.pop("required", True)]
    return {"type": "object", "properties": props, "required": required}


_REPO = {"type": "string", "description": "path to the repository"}

TOOLS = {
    "index_repository": (
        _index_repository,
        "Index a Python repo into Proofbraid's dependency graph; returns "
        "files indexed and symbols found.",
        lambda: _schema(repo_path=dict(_REPO)),
    ),
    "get_graph": (
        _get_graph,
        f"Return symbols and their call edges (first {GRAPH_LIMIT} symbols).",
        lambda: _schema(repo_path=dict(_REPO)),
    ),
    "verify_repo": (
        _verify_repo,
        "Run verification (compile, lint when installed, tests); returns "
        "pass or fail plus the failure output.",
        lambda: _schema(repo_path=dict(_REPO)),
    ),
    "propose_change": (
        _propose_change,
        "Ask a model for a change, verify it in a disposable copy, record "
        "the hypothesis. Dry run by default; apply happens only after a "
        "verified pass.",
        lambda: _schema(
            repo_path=dict(_REPO),
            task={"type": "string", "description": "what to change"},
            provider={"type": "string", "required": False,
                      "enum": ["meta", "baseten", "fireworks", "custom", "all"],
                      "description": "same names as --provider, default meta"},
            apply={"type": "boolean", "required": False,
                   "description": "write to the real repo after a verified "
                                  "pass (default false)"},
        ),
    ),
    "list_hypotheses": (
        _list_hypotheses,
        "Return the recorded hypotheses (verified and failed).",
        lambda: _schema(repo_path=dict(_REPO)),
    ),
}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}}


def handle(message) -> dict | None:
    """Handle one decoded JSON-RPC message. Returns None for notifications."""
    if not isinstance(message, dict) or not isinstance(message.get("method"), str):
        return _error(message.get("id") if isinstance(message, dict) else None,
                      -32600, "invalid request")
    msg_id = message.get("id")
    if "id" not in message:
        return None  # notification, e.g. notifications/initialized
    method = message["method"]
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(msg_id, -32602, "params must be an object")
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "proofbraid", "version": __version__}}
    elif method == "tools/list":
        result = {"tools": [
            {"name": name, "description": desc, "inputSchema": schema()}
            for name, (_, desc, schema) in TOOLS.items()]}
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or name not in TOOLS:
            return _error(msg_id, -32602, f"unknown tool {name!r}")
        if not isinstance(args, dict):
            return _error(msg_id, -32602, "arguments must be an object")
        missing = [k for k in TOOLS[name][2]()["required"] if k not in args]
        if missing:
            return _error(msg_id, -32602,
                          f"missing arguments: {', '.join(missing)}")
        try:
            output, failed = TOOLS[name][0](args), False
        except Exception as exc:  # report tool failures, never crash the server
            output, failed = {"error": f"{type(exc).__name__}: {exc}"}, True
        if isinstance(output, dict) and output.get("ok") is False:
            failed = True
        result = {"content": [{"type": "text", "text": json.dumps(output, indent=2)}],
                  "isError": failed}
    else:
        return _error(msg_id, -32601, f"method not found: {method}")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(stdin=None, stdout=None) -> None:
    """Read JSON-RPC lines from stdin and write responses to stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, f"parse error: {exc}")
        else:
            # Keep stray prints from tools off the protocol stream.
            with contextlib.redirect_stdout(sys.stderr):
                response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
