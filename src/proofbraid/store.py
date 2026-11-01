"""Hypothesis memory with eviction.

A hypothesis is one proposed change: the task, the files it touched, the
content hashes it was verified against, and whether verification passed.
The rule that makes this a *memory* instead of a log:

- When a hypothesis for a task verifies, every older hypothesis touching
  the same files is evicted (superseded). Stale, contradicted context is
  removed before it can be retrieved again — this is the eviction step
  from the hackathon proposal.
- Failed hypotheses are kept (marked failed) so the agent does not retry
  the exact same change blindly, but they are never retrieved as context.

Stored as plain JSON under ``.proofbraid/hypotheses.json`` in the repo.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Hypothesis:
    id: str
    task: str
    files: list[str]
    hashes: dict[str, str]
    verified: bool
    failures: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


def hypotheses_path(root: Path) -> Path:
    return root.resolve() / ".proofbraid" / "hypotheses.json"


def load_hypotheses(root: Path) -> list[Hypothesis]:
    path = hypotheses_path(root)
    if not path.exists():
        return []
    return [Hypothesis(**h) for h in json.loads(path.read_text(encoding="utf-8"))]


def save_hypotheses(root: Path, hypotheses: list[Hypothesis]) -> None:
    path = hypotheses_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(h) for h in hypotheses], indent=2), encoding="utf-8"
    )


def record_hypothesis(
    root: Path,
    task: str,
    files: list[str],
    hashes: dict[str, str],
    verified: bool,
    failures: list[str] | None = None,
) -> Hypothesis:
    """Save a hypothesis; on verified success, evict superseded ones."""
    hypotheses = load_hypotheses(root)
    if verified:
        touched = set(files)
        hypotheses = [
            h
            for h in hypotheses
            if not (set(h.files) & touched and h.verified)
        ]
    hyp = Hypothesis(
        id=uuid.uuid4().hex[:8],
        task=task,
        files=sorted(files),
        hashes=hashes,
        verified=verified,
        failures=failures or [],
    )
    hypotheses.append(hyp)
    save_hypotheses(root, hypotheses)
    return hyp
