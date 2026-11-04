"""The propose loop: retrieve -> ask -> verify in a temp copy -> save/evict.

Safety invariant: the real repo is NEVER written unless verification
passed AND the user passed --apply. By default this is dry-run: the
proposal is verified in a disposable copy and the outcome is reported.
Optional diff mode validates and applies all hunks in memory first.

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
import re
import shutil
import sys
import tempfile
from pathlib import Path, PureWindowsPath

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

MAX_RETRY = 5
RETRY_ERROR_LIMIT = 4000

DIFF_SYSTEM_PROMPT = (
    "You are a precise coding assistant. You will receive a task and the "
    "structurally related code spans from a real repository (symbols, "
    "their files, and what they call). Rewrite ONLY the files that need "
    "to change. Reply with a single JSON object: {\"files\": [{\"path\": "
    "\"relative/path.py\", \"diff\": \"unified diff\"}]}. Each diff "
    "must contain exactly one --- a/path and +++ b/path header pair, "
    "followed by valid @@ hunk headers. Use /dev/null only as the old "
    "path for a new file; do not "
    "delete files. Include no prose outside the JSON object."
)


def apply_unified_diff(original: str, diff: str) -> str:
    """Apply a strict, exact-context unified diff to one file's text."""
    if not isinstance(diff, str):
        raise ValueError("diff must be text")
    lines = diff.splitlines()
    if (len(lines) < 3 or not lines[0].startswith("--- ")
            or not lines[1].startswith("+++ ")):
        raise ValueError("missing or malformed file headers")
    old_path = lines[0][4:].split("\t", 1)[0]
    new_path = lines[1][4:].split("\t", 1)[0]
    if not old_path or not new_path:
        raise ValueError("empty file header path")
    if new_path == "/dev/null":
        raise ValueError("file deletion is not supported")
    if old_path == "/dev/null" and original:
        raise ValueError("new file diff requires an empty source")
    if old_path != "/dev/null":
        old_name = old_path[2:] if old_path.startswith("a/") else old_path
        new_name = new_path[2:] if new_path.startswith("b/") else new_path
        if old_name != new_name:
            raise ValueError("file headers refer to different paths")

    source = original.splitlines()
    result = []
    source_pos = 0
    line_pos = 2
    last_old_start = None
    hunk_count = 0
    hunk_re = re.compile(
        r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")
    while line_pos < len(lines):
        match = hunk_re.fullmatch(lines[line_pos])
        if not match:
            raise ValueError("malformed hunk header")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or 1)
        new_start = int(match.group(3))
        new_count = int(match.group(4) or 1)
        if (old_start < (1 if old_count else 0)
                or new_start < (1 if new_count else 0)):
            raise ValueError("invalid hunk line position")
        if last_old_start is not None and old_start <= last_old_start:
            raise ValueError("hunks are not in ascending order")
        old_index = old_start if old_count == 0 else old_start - 1
        if old_index < source_pos or old_index > len(source):
            raise ValueError("hunk overlaps or starts beyond the source")
        result.extend(source[source_pos:old_index])
        source_pos = old_index
        expected_new_start = len(result) + (1 if new_count else 0)
        if new_start != expected_new_start:
            raise ValueError("hunk new line position does not match")

        line_pos += 1
        seen_old = 0
        seen_new = 0
        while line_pos < len(lines) and not lines[line_pos].startswith("@@"):
            line = lines[line_pos]
            if line == "\\ No newline at end of file":
                line_pos += 1
                continue
            if not line or line[0] not in " +-":
                raise ValueError("malformed hunk line")
            marker, text = line[0], line[1:]
            if marker in " -":
                if source_pos >= len(source) or source[source_pos] != text:
                    reason = "context line" if marker == " " else "removed line"
                    raise ValueError(f"{reason} does not match the source")
                source_pos += 1
                seen_old += 1
            if marker in " +":
                seen_new += 1
            if marker != "-":
                result.append(text)
            line_pos += 1

        if seen_old != old_count or seen_new != new_count:
            raise ValueError("hunk line counts do not match the header")
        last_old_start = old_start
        hunk_count += 1

    if not hunk_count:
        raise ValueError("diff contains no hunks")
    result.extend(source[source_pos:])
    newline = "\r\n" if "\r\n" in original else (
        "\r" if "\r" in original and "\n" not in original else "\n")
    updated = newline.join(result)
    if original.endswith(("\n", "\r")):
        updated += newline
    return updated


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
                provider: str = "meta", sandbox: str = "local",
                diff: bool = False, retry: int = 1) -> dict:
    """Run one propose cycle with up to `retry` attempts (clamped to 1..5).
    Returns a report dict; never raises for proposal failures — failures
    are reported, so the CLI can print them."""
    retry = max(1, min(int(retry), MAX_RETRY))
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
    mode = "diff" if diff else "full"
    system = DIFF_SYSTEM_PROMPT if diff else SYSTEM_PROMPT

    if provider == "all":
        chain = ["meta", "baseten", "fireworks"]
    elif provider in providers.PROVIDERS:
        chain = [provider]
    else:
        return {"ok": False,
                "error": f"unknown provider {provider!r}",
                "provider_attempts": [], "mode": mode}
    proposal = None
    attempts: list[dict] = []
    provider_used = None
    for name in chain:
        try:
            proposal = providers.chat_json(name, system, prompt)
            provider_used = name
            break
        except providers.ProviderError as exc:
            attempts.append({"provider": name, "error": str(exc)})
    if proposal is None:
        return {"ok": False, "error": "all providers failed",
                "provider_attempts": attempts, "mode": mode}
    # Snapshot usage now: the aux call later would overwrite LAST_USAGE.
    usage = dict(providers.LAST_USAGE)
    base = {"provider": provider_used, "provider_attempts": attempts,
            "mode": mode}
    log: list[dict] = []
    files: list = []
    last_error = ""
    report: dict = {}
    for number in range(1, retry + 1):
        if number > 1:
            # Same provider as attempt 1; never switch between retries.
            try:
                proposal = providers.chat_json(
                    provider_used, system,
                    build_retry_prompt(prompt, files, last_error, diff))
            except providers.ProviderError as exc:
                report = {"ok": False, "error": str(exc), **base, "usage": usage}
                log.append({"attempt": number, "ok": False,
                            "error": str(exc)[:RETRY_ERROR_LIMIT]})
                continue
            usage = dict(providers.LAST_USAGE)
        files = proposal.get("files", []) if isinstance(proposal, dict) else []
        report = _attempt(root, task, files, diff, sandbox, apply,
                          {**base, "usage": usage}, number)
        if report["ok"]:
            log.append({"attempt": number, "ok": True})
            break
        last_error = ("\n".join(report["failures"]) if report.get("failures")
                      else report["error"])
        log.append({"attempt": number, "ok": False,
                    "error": last_error[:RETRY_ERROR_LIMIT]})
        if report.pop("_fatal", False):
            break
    hashes = report.pop("_hashes", {})
    if not report["ok"] and ("failures" in report or retry > 1):
        store.record_hypothesis(
            root, task, report.get("files", []), hashes, verified=False,
            failures=report.get("failures") or [report["error"]],
            attempts_used=len(log),
        )
    report.update(attempts_used=len(log), attempts=log)
    return report


def build_retry_prompt(prompt: str, files: list, error: str, diff: bool) -> str:
    """Follow-up prompt after a failed attempt, in the original output mode."""
    kind = "unified diffs" if diff else "full file contents"
    return (
        f"{prompt}\n\nYOUR PREVIOUS PROPOSAL ({kind}) FAILED VERIFICATION:\n"
        f"{json.dumps({'files': files}, indent=2)}\n\n"
        f"VERIFICATION OUTPUT:\n{error[:RETRY_ERROR_LIMIT]}\n\n"
        f"Fix the reported problem and return a corrected proposal as "
        f"{kind}, in exactly the same JSON format as before. Any diff must "
        f"apply to the ORIGINAL repository files shown above."
    )


def _attempt(root: Path, task: str, files, diff: bool, sandbox: str,
             apply: bool, base: dict, attempt: int) -> dict:
    """Stage one proposal in a fresh temp copy and verify it. Records and
    applies only on a verified pass; failures are left to the caller."""
    provider_used = base["provider"]
    attempts = base["provider_attempts"]
    usage = base["usage"]
    mode = base["mode"]
    if not isinstance(files, list) or not files:
        return {"ok": False, "error": "model returned no files", **base}

    # Verify in a disposable copy, never the real repo.
    with tempfile.TemporaryDirectory(prefix="proofbraid-") as tmp:
        tmp_path = Path(tmp) / "repo"
        shutil.copytree(root, tmp_path, ignore=shutil.ignore_patterns(".proofbraid"))
        changed = []
        if diff:
            new_contents = {}
            for entry in files:
                rel = entry.get("path", "") if isinstance(entry, dict) else ""
                label = rel if isinstance(rel, str) and rel else "<unknown>"
                path = Path(rel) if isinstance(rel, str) else Path()
                windows_path = (PureWindowsPath(rel) if isinstance(rel, str)
                                else PureWindowsPath())
                if (not isinstance(entry, dict) or not isinstance(rel, str)
                        or not rel or "\0" in rel or "\\" in rel
                        or path.is_absolute()
                        or windows_path.is_absolute() or windows_path.drive
                        or ".." in path.parts or ".." in windows_path.parts
                        or not path.parts):
                    return {"ok": False,
                            "error": f"malformed diff for {label}: unsafe file path",
                            "provider": provider_used, "provider_attempts": attempts,
                            "usage": usage, "mode": mode}
                if rel in new_contents:
                    return {"ok": False,
                            "error": f"malformed diff for {rel}: duplicate file entry",
                            "provider": provider_used, "provider_attempts": attempts,
                            "usage": usage, "mode": mode}
                diff_text = entry.get("diff")
                target = tmp_path / rel
                try:
                    original = target.read_bytes().decode("utf-8") if target.exists() else ""
                    new_contents[rel] = apply_unified_diff(original, diff_text)
                except (OSError, UnicodeError, ValueError) as exc:
                    return {"ok": False,
                            "error": f"malformed diff for {rel}: {exc}",
                            "provider": provider_used, "provider_attempts": attempts,
                            "usage": usage, "mode": mode}
            for rel, content in new_contents.items():
                target = tmp_path / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content.encode("utf-8"))
                changed.append(rel)
        else:
            for entry in files:
                rel = entry.get("path", "")
                content = entry.get("content")
                if not rel or content is None or ".." in Path(rel).parts:
                    return {"ok": False, "error": f"unsafe or empty file entry: {rel!r}",
                            "provider": provider_used, "mode": mode}
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
                    "provider": provider_used, "mode": mode, "_fatal": True}
        if failures:
            return {"ok": False, "verified": False, "files": changed,
                    "failures": failures, "provider": provider_used,
                    "verified_via": via, "provider_attempts": attempts,
                    "usage": usage, "mode": mode,
                    "_hashes": _hashes(tmp_path, changed)}
        hyp = store.record_hypothesis(
            root, task, changed, _hashes(tmp_path, changed), verified=True,
            attempts_used=attempt,
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
                "provider_attempts": attempts, "usage": usage, "mode": mode}
