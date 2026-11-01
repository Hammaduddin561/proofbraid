"""The propose loop: retrieve -> ask -> verify in a temp copy -> save/evict.

Safety invariant: the real repo is NEVER written unless verification
passed AND the user passed --apply. By default this is dry-run: the
proposal is verified in a disposable copy and the outcome is reported.

All-partner wiring: the proposer is chosen by provider. "meta" (default)
is the primary path; "baseten" is the open-weights path; "fireworks" is
available as a proposer too; "all" cascades meta -> baseten -> fireworks
until one returns a proposal. Verification runs locally by default, or
in a Modal sandbox with --sandbox modal (falls back to local, and says
so, when Modal is not configured).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from . import aux as aux_mod
from . import graph as graph_mod
from . import providers
from . import sandbox as sandbox_mod
from . import store, verify
from . import vectors as vectors_mod


def _refresh_vectors(graph) -> None:
    """Rebuild vector shards after a re-index. Never blocks the caller."""
    try:
        vectors_mod.build_vectors(graph)
    except Exception:
        pass

SYSTEM_PROMPT = (
    "You are a precise coding assistant. You will receive a task and the "
    "structurally related code spans from a real repository (symbols, "
    "their files, and what they call). Rewrite ONLY the files that need "
    "to change. Reply with a single JSON object: {\"files\": [{\"path\": "
    "\"relative/path.py\", \"content\": \"full new file content\"}]}. "
    "Do not invent functions, imports, or interfaces that are not in the "
    "spans you were given. Preserve everything else byte for byte."
)


def _hashes(root: Path, files: list[str]) -> dict[str, str]:
    out = {}
    for rel in files:
        path = root / rel
        if path.exists():
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def build_prompt(task: str, spans: list[graph_mod.Symbol], root: Path) -> str:
    parts = [f"TASK: {task}\n", "RELATED CODE SPANS (from the repo's AST graph):"]
    for sym in spans:
        path = root / sym.file
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            snippet = "\n".join(lines[max(0, sym.line - 1): sym.line + 14])
        except OSError:
            snippet = "(unreadable)"
        parts.append(
            f"\n--- {sym.kind} {sym.name} in {sym.file}:{sym.line} "
            f"(calls: {', '.join(sym.calls) or 'none'}) ---\n{snippet}"
        )
    return "\n".join(parts)


def run_propose(root: Path, task: str, apply: bool = False,
                provider: str = "meta", sandbox: str = "local") -> dict:
    """Run one propose cycle. Returns a report dict; never raises for
    proposal failures — failures are reported, so the CLI can print them."""
    root = root.resolve()
    graph = graph_mod.load_graph(root) or graph_mod.index_repo(root)
    stale = graph_mod.stale_files(graph, root)
    if stale:
        graph = graph_mod.index_repo(root)
        graph_mod.save_graph(graph)
        _refresh_vectors(graph)
    try:
        spans = vectors_mod.retrieve_hybrid(graph, task, root)
    except Exception:
        # Retrieval must never block the propose loop; lexical is proven.
        spans = graph_mod.retrieve_spans(graph, task)
    prompt = build_prompt(task, spans, root)

    if provider == "all":
        chain = ["meta", "baseten", "fireworks"]
    elif provider in providers.PROVIDERS:
        chain = [provider]
    else:
        return {"ok": False,
                "error": f"unknown provider {provider!r}",
                "provider_attempts": []}
    proposal = None
    attempts: list[dict] = []
    provider_used = None
    for name in chain:
        try:
            proposal = providers.chat_json(name, SYSTEM_PROMPT, prompt)
            provider_used = name
            break
        except providers.ProviderError as exc:
            attempts.append({"provider": name, "error": str(exc)})
    if proposal is None:
        return {"ok": False, "error": "all providers failed",
                "provider_attempts": attempts}
    # Snapshot usage now: the aux call later would overwrite LAST_USAGE.
    usage = dict(providers.LAST_USAGE)
    files = proposal.get("files", []) if isinstance(proposal, dict) else []
    if not isinstance(files, list) or not files:
        return {"ok": False, "error": "model returned no files",
                "provider": provider_used, "provider_attempts": attempts,
                "usage": usage}

    # Verify in a disposable copy, never the real repo.
    with tempfile.TemporaryDirectory(prefix="proofbraid-") as tmp:
        tmp_path = Path(tmp) / "repo"
        shutil.copytree(root, tmp_path, ignore=shutil.ignore_patterns(".proofbraid"))
        changed = []
        for entry in files:
            rel = entry.get("path", "")
            content = entry.get("content")
            if not rel or content is None or ".." in Path(rel).parts:
                return {"ok": False, "error": f"unsafe or empty file entry: {rel!r}",
                        "provider": provider_used}
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            changed.append(rel)
        if sandbox == "modal":
            failures, via = sandbox_mod.verify_in_sandbox(tmp_path)
        elif sandbox == "local":
            failures, via = verify.verify_repo(tmp_path), "local"
        else:
            return {"ok": False, "error": f"unknown sandbox {sandbox!r}",
                    "provider": provider_used}
        if failures:
            store.record_hypothesis(
                root, task, changed, _hashes(tmp_path, changed),
                verified=False, failures=failures,
            )
            return {"ok": False, "verified": False, "files": changed,
                    "failures": failures, "provider": provider_used,
                    "verified_via": via, "provider_attempts": attempts,
                    "usage": usage}
        hyp = store.record_hypothesis(
            root, task, changed, _hashes(tmp_path, changed), verified=True
        )
        if apply:
            for rel in changed:
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(tmp_path / rel, dest)
            graph_mod.save_graph(graph_mod.index_repo(root))
            _refresh_vectors(graph_mod.load_graph(root))
        return {"ok": True, "verified": True, "files": changed,
                "hypothesis": hyp.id, "applied": apply,
                "provider": provider_used, "verified_via": via,
                "aux_summary": aux_mod.summarize_hypothesis(task, changed),
                "provider_attempts": attempts, "usage": usage}
