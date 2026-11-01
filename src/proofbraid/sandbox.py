"""Sandboxed verification via Modal (with a local fallback).

Role in the all-partner architecture: Modal is the *execution* partner.
Model-generated code is untrusted code; running its test suite in an
isolated sandbox is strictly safer than running it next to the
developer's real files, and it is a capability none of the inference
partners duplicate.

How it works here:

- Default (MODAL_SANDBOX unset, or the `modal` package / tokens absent):
  verification runs locally in a disposable temp copy, exactly as before.
  The report says so explicitly (`verified_via: "local"` or
  `"local-fallback (reason)"`) — a fallback is never silently presented
  as a sandbox run.
- With MODAL_SANDBOX=1, the `modal` package installed, and
  MODAL_TOKEN_ID / MODAL_TOKEN_SECRET set: the repo copy's files are
  transferred into a fresh Modal sandbox (base64 over exec, so no
  shell-quoting hazards), the same checks run there (py_compile, then
  pytest or unittest), output is collected, and the sandbox is
  terminated in a `finally` block.

CAVEAT: the live Modal path follows Modal's Sandbox API shape
(Sandbox.create / exec / terminate) as documented by Modal, but it has
NOT been run against real Modal credentials in this scaffold. Before
demo day, verify it against Modal's official Sandboxes documentation
and a real token; any API mismatch degrades to local verification with
an explicit reason, never to a false "verified".
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from . import verify as verify_mod


def _local(repo_copy: Path, reason: str = "") -> tuple[list[str], str]:
    via = "local" if not reason else f"local-fallback ({reason})"
    return verify_mod.verify_repo(repo_copy), via


def _modal_run(repo_copy: Path) -> tuple[list[str], str]:
    """Run the checks inside a Modal sandbox. Raises on any failure so
    the caller can fall back explicitly."""
    import modal

    app = modal.App.lookup("proofbraid-sandbox", create_if_missing=True)
    image = modal.Image.debian_slim().pip_install("pytest")
    sandbox = modal.Sandbox.create(app=app, image=image)
    try:
        # Transfer files: base64 avoids every shell-quoting hazard.
        for path in sorted(repo_copy.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(repo_copy))
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            proc = sandbox.exec(
                "sh", "-c",
                f"mkdir -p /repo/$(dirname '{rel}') && "
                f"echo {encoded} | base64 -d > /repo/{rel}",
            )
            proc.wait()
        failures: list[str] = []
        for cmd in (
            ["python", "-m", "py_compile"]
            + [f"/repo/{p}" for p in sorted(repo_copy.rglob("*.py"))
               if "__pycache__" not in p.parts],
            ["python", "-m", "pytest", "-q", "/repo"],
        ):
            proc = sandbox.exec(*cmd, workdir="/repo")
            proc.wait()
            out = proc.stdout.read() + proc.stderr.read()
            if proc.returncode != 0:
                failures.append(
                    f"sandbox check failed ({' '.join(cmd[:3])}):\n{out[-2000:]}"
                )
        return failures, "modal-sandbox"
    finally:
        sandbox.terminate()


def verify_in_sandbox(repo_copy: Path) -> tuple[list[str], str]:
    """Verify a repo copy, preferring a Modal sandbox when configured.

    Returns (failures, via). Never raises for missing configuration —
    missing config is a fallback with a reason, not a crash.
    """
    if os.environ.get("MODAL_SANDBOX", "") != "1":
        return _local(repo_copy)
    try:
        import modal  # noqa: F401
    except ImportError:
        return _local(repo_copy, "modal package not installed")
    if not (os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET")):
        return _local(repo_copy, "Modal tokens not set")
    try:
        return _modal_run(repo_copy)
    except Exception as exc:  # API drift, auth failure, sandbox error
        return _local(repo_copy, f"Modal run failed ({exc}); fell back")
