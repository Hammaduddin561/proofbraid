"""Pre-execution verification loop.

Runs, in order, against a repo copy (never the real repo directly):

1. ``py_compile`` on every .py file — catches syntax breakage.
2. An optional linter (ruff, then flake8) if one is installed; skipped
   gracefully when absent, never a hard failure.
3. The repo's own test suite: pytest if installed and tests exist,
   otherwise unittest discovery. No tests found is not a failure — it
   just means step 3 contributes nothing.

Returns a list of failure strings; empty means verified.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, f"timed out after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError as exc:
        return 1, f"command not found: {exc.filename}"


def verify_repo(root: Path) -> list[str]:
    """Verify a repo copy. Returns failures (empty = verified)."""
    root = root.resolve()
    failures: list[str] = []

    py_files = [
        p for p in sorted(root.rglob("*.py"))
        if "__pycache__" not in p.parts and ".proofbraid" not in p.parts
    ]
    for path in py_files:
        code, out = _run(
            [sys.executable, "-m", "py_compile", str(path)], cwd=root
        )
        if code != 0:
            failures.append(f"compile failed: {path.relative_to(root)}:\n{out}")

    linter = shutil.which("ruff") or shutil.which("flake8")
    if linter:
        code, out = _run([linter, "check", "."] if "ruff" in linter else [linter, "."],
                         cwd=root)
        if code != 0:
            failures.append(f"lint failed ({Path(linter).name}):\n{out}")

    has_tests = any(
        p.name.startswith("test_") or p.name.endswith("_test.py")
        for p in py_files
    ) or (root / "tests").exists()
    if has_tests:
        if importlib.util.find_spec("pytest"):
            code, out = _run(
                [sys.executable, "-m", "pytest", "-q"], cwd=root, timeout=300
            )
        else:
            code, out = _run(
                [sys.executable, "-m", "unittest", "discover", "-q"],
                cwd=root, timeout=300,
            )
        if code != 0:
            failures.append(f"tests failed:\n{out[-2000:]}")

    return failures
