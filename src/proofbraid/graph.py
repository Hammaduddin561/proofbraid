"""AST dependency graph indexer.

Today this covers Python via the stdlib ``ast`` module: for every ``.py``
file it records top-level functions, classes and methods, module imports,
and a call graph (which local symbol calls which). That is the structure
vector retrieval cannot see: who imports whom, and which interfaces a
change would actually break.

SEAM FOR OTHER LANGUAGES: ``index_file`` is the only Python-specific
function. A tree-sitter backend would replace it with a per-language
parser producing the same ``Symbol``/edge shape; everything downstream
(retrieval, prompting, verification) is language-agnostic.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

IGNORE_DIRS = {
    ".git", ".proofbraid", "__pycache__", ".venv", "venv", "node_modules",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".eggs",
}


@dataclass
class Symbol:
    """A named code unit: function, class, or method."""
    name: str
    kind: str  # "function" | "class" | "method"
    file: str
    line: int
    calls: list[str] = field(default_factory=list)
    end_line: int = 0  # last line of the body; 0 = unknown (old indexes)


@dataclass
class Graph:
    root: str
    files: dict[str, str] = field(default_factory=dict)  # rel path -> sha256
    symbols: list[Symbol] = field(default_factory=list)
    imports: dict[str, list[str]] = field(default_factory=dict)  # file -> modules
    git_head: str | None = None  # HEAD commit at index time, if a git repo
    git_revs: dict[str, str] = field(default_factory=dict)  # rel -> last commit

    def to_json(self) -> str:
        data = asdict(self)
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Graph":
        data = json.loads(text)
        data["symbols"] = [Symbol(**s) for s in data["symbols"]]
        return cls(**data)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_revs(root: Path, files: list[str]) -> tuple[str | None, dict[str, str]]:
    """Provenance: HEAD commit + last commit touching each file.

    Returns (None, {}) when root is not a git repo or git is unavailable.
    Never raises: provenance is metadata, not a reason to fail indexing.
    """
    import subprocess

    if not (root / ".git").exists():
        return None, {}
    try:
        head_proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            text=True, timeout=10)
        head = head_proc.stdout.strip() if head_proc.returncode == 0 else None
        revs: dict[str, str] = {}
        for rel in files:
            proc = subprocess.run(
                ["git", "log", "-1", "--format=%H", "--", rel], cwd=root,
                capture_output=True, text=True, timeout=10)
            if proc.returncode == 0 and proc.stdout.strip():
                revs[rel] = proc.stdout.strip()
        return head, revs
    except (OSError, subprocess.SubprocessError):
        return None, {}


def _index_python_file(path: Path, rel: str) -> tuple[list[Symbol], list[str]]:
    """Parse one Python file. Unparseable files yield no symbols, never crash."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return [], []
    symbols: list[Symbol] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imports.append(alias.name)
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            symbols.append(_symbol_from_func(node, rel, "function"))
        elif isinstance(node, ast.ClassDef):
            symbols.append(Symbol(node.name, "class", rel, node.lineno,
                                  end_line=node.end_lineno or node.lineno))
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    symbols.append(_symbol_from_func(item, rel, "method"))
    return symbols, sorted(set(imports))


def _symbol_from_func(node: ast.FunctionDef, rel: str, kind: str) -> Symbol:
    calls = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                calls.add(func.attr)
    return Symbol(node.name, kind, rel, node.lineno, sorted(calls),
                  end_line=node.end_lineno or node.lineno)


def index_repo(root: Path) -> Graph:
    """Index every .py file under root, skipping VCS/venv/cache dirs."""
    root = root.resolve()
    graph = Graph(root=str(root))
    for path in sorted(root.rglob("*.py")):
        if any(part in IGNORE_DIRS for part in path.relative_to(root).parts):
            continue
        rel = str(path.relative_to(root))
        graph.files[rel] = _sha256(path)
        symbols, imports = _index_python_file(path, rel)
        graph.symbols.extend(symbols)
        graph.imports[rel] = imports
    head, revs = _git_revs(root, sorted(graph.files))
    graph.git_head = head
    graph.git_revs = revs
    return graph


def graph_path(root: Path) -> Path:
    return root.resolve() / ".proofbraid" / "graph.json"


def save_graph(graph: Graph) -> Path:
    path = graph_path(Path(graph.root))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(graph.to_json(), encoding="utf-8")
    return path


def load_graph(root: Path) -> Graph | None:
    path = graph_path(root)
    if not path.exists():
        return None
    return Graph.from_json(path.read_text(encoding="utf-8"))


def lexical_ranked(graph: Graph, task: str) -> list[Symbol]:
    """Symbols ordered by lexical match against the task words.

    Shared by plain lexical retrieval and hybrid retrieval so both rank
    the lexical signal identically.
    """
    words = {w.strip("()[]{}:,.`'\"").lower() for w in task.split()}
    words = {w for w in words if len(w) > 2}
    scored: list[tuple[int, Symbol]] = []
    for sym in graph.symbols:
        score = 0
        name = sym.name.lower()
        for w in words:
            if w in name or name in w:
                score += 3
            if any(w in c.lower() for c in sym.calls):
                score += 1
        if score:
            scored.append((score, sym))
    scored.sort(key=lambda item: -item[0])
    return [sym for _, sym in scored]


def retrieve_spans(graph: Graph, task: str, limit: int = 8) -> list[Symbol]:
    """Retrieve symbols structurally related to the task.

    Lexical match on the task words against symbol names, boosted when the
    symbol's file is imported by (or imports) another matched file. This is
    deliberately structural: a symbol mentioned in the task plus its
    callers/callees, not a similarity ranking over the whole repo.
    """
    picked = lexical_ranked(graph, task)[:limit]
    # Add direct callees of picked symbols: the interfaces a change touches.
    picked_names = {s.name for s in picked}
    for sym in graph.symbols:
        if sym.name in picked_names:
            continue
        if any(sym.name in p.calls for p in picked):
            picked.append(sym)
    return picked[: limit + 4]


def stale_files(graph: Graph, root: Path) -> list[str]:
    """Files whose content hash changed since indexing (stale context)."""
    root = root.resolve()
    stale = []
    for rel, old_hash in graph.files.items():
        path = root / rel
        if not path.exists() or _sha256(path) != old_hash:
            stale.append(rel)
    return stale
