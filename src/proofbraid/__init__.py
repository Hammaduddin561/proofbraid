"""Proofbraid: a local, self-correcting coding agent.

Maps a repo's AST dependency graph, proposes changes via Meta's Model API,
and verifies every proposal in a disposable copy before it can touch the
real repo. Pure stdlib, no third-party dependencies.
"""

__version__ = "0.1.0"
