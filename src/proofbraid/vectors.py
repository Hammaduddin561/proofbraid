"""Hybrid retrieval: lexical evidence fused with local vector evidence.

Design transferred from prior research on evidence-grounded retrieval
(the v5 hybrid reranker pattern), re-implemented here from scratch for
Proofbraid during the hackathon — no earlier code was copied in:

- Two independent direct signals are fused with reciprocal rank fusion
  (RRF, k=60). Raw lexical and cosine scores are never added directly,
  because their scales are unrelated.
- Code-graph neighbours (callees) are added only AFTER the direct
  results, so a high-degree symbol cannot displace a better direct hit.
- Every returned span is still a current source location with a hash and
  line range. Similarity is retrieval help only: it is never evidence,
  never a factual claim, and never bypasses the verification gate.
- Vectors for stale files are omitted from vector results entirely.

HONEST ADAPTATION: the prior work used a neural embedding model
(nomic-embed-text via a local Ollama endpoint, 768 dims). Proofbraid stays
pure stdlib with zero dependencies, so its vectors are hashed TF-IDF
vectors (256 dims), not neural embeddings. The transfer is the fusion
method, the stale-omission rule, and the graph-after-direct ordering —
not the embedding model. Do not describe these as neural embeddings.

Status: experimental. The prior work measured its reranker on a small
suite only and did not promote it to a default path without a larger
held-out evaluation. Proofbraid mirrors that caution: hybrid retrieval is
used when vectors exist, lexical retrieval is the proven fallback, and
no retrieval-quality improvement is claimed until measured.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from . import graph as graph_mod

DIM = 256
RRF_K = 60
MODEL_ID = "hashed-tfidf-stdlib-v1"

_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_STOP = {
    "def", "class", "return", "import", "from", "if", "elif", "else",
    "for", "while", "in", "is", "not", "and", "or", "none", "true",
    "false", "self", "pass", "try", "except", "finally", "with", "as",
    "lambda", "yield", "raise", "assert", "del", "global", "nonlocal",
}


def tokenize(text: str) -> list[str]:
    """Split code/text into lowercase identifier tokens."""
    tokens: list[str] = []
    for part in re.split(r"[^A-Za-z0-9]+", text):
        if not part:
            continue
        for piece in _CAMEL.findall(part):
            tok = piece.lower()
            if len(tok) >= 2 and tok not in _STOP:
                tokens.append(tok)
    return tokens


def _hash_token(token: str) -> int:
    return int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:8], 16) % DIM


def compute_idf(token_lists: list[list[str]]) -> dict[str, float]:
    """Smoothed inverse document frequency over symbol token lists."""
    doc_count = len(token_lists) or 1
    df: Counter = Counter()
    for tokens in token_lists:
        for tok in set(tokens):
            df[tok] += 1
    return {
        tok: math.log((doc_count + 1) / (count + 1)) + 1.0
        for tok, count in df.items()
    }


def embed(tokens: list[str], idf: dict[str, float]) -> list[float]:
    """Hashed TF-IDF vector, L2-normalized for cosine similarity."""
    vec = [0.0] * DIM
    for tok, count in Counter(tokens).items():
        vec[_hash_token(tok)] += count * idf.get(tok, 1.0)
    norm = math.sqrt(sum(x * x for x in vec))
    if norm:
        vec = [x / norm for x in vec]
    return vec


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def vectors_dir(root: Path) -> Path:
    return Path(root).resolve() / ".proofbraid" / "vectors"


def _key(sym: graph_mod.Symbol) -> str:
    return f"{sym.file}:{sym.line}:{sym.name}"


def _symbol_text(graph: graph_mod.Graph, sym: graph_mod.Symbol) -> str:
    """Source text of a symbol: name plus its body lines."""
    path = Path(graph.root) / sym.file
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return sym.name
    start = max(sym.line - 1, 0)
    if sym.end_line and sym.end_line > sym.line:
        end = sym.end_line
    else:
        later = [s.line for s in graph.symbols
                 if s.file == sym.file and s.line > sym.line]
        end = min(later) - 1 if later else len(lines)
    return sym.name + "\n" + "\n".join(lines[start:end])


def build_vectors(graph: graph_mod.Graph) -> tuple[int, int]:
    """Build per-file vector shards + a manifest. Returns (symbols, files).

    Shard files are named by the SHA-256 of the source file's relative
    path, and each shard records the source file's content hash so a
    stale shard can be detected and omitted at load time.
    """
    root = Path(graph.root)
    out_dir = vectors_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    token_lists: dict[str, list[str]] = {}
    for sym in graph.symbols:
        token_lists[_key(sym)] = tokenize(_symbol_text(graph, sym))
    idf = compute_idf(list(token_lists.values()))
    by_file: dict[str, list[graph_mod.Symbol]] = {}
    for sym in graph.symbols:
        by_file.setdefault(sym.file, []).append(sym)
    for rel, syms in by_file.items():
        shard = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16]
        data = {
            "file": rel,
            "file_sha256": graph.files.get(rel),
            "vectors": {
                _key(sym): [round(x, 4) for x in embed(token_lists[_key(sym)], idf)]
                for sym in syms
            },
        }
        (out_dir / f"{shard}.json").write_text(
            json.dumps(data), encoding="utf-8")
    (out_dir / "_meta.json").write_text(json.dumps({
        "dim": DIM, "idf": idf, "model": MODEL_ID,
        "files": sorted(graph.files),
    }), encoding="utf-8")
    return len(graph.symbols), len(by_file)


def load_vectors(graph: graph_mod.Graph, root: Path) -> tuple[dict | None, dict]:
    """Load manifest + vectors, omitting shards whose source went stale.

    A shard is trusted only when the file on disk still hashes to the
    hash recorded in the shard. Returns (meta, {symbol_key: vector}).
    (None, {}) when no index exists.
    """
    root = Path(root).resolve()
    out_dir = vectors_dir(root)
    meta_path = out_dir / "_meta.json"
    if not meta_path.exists():
        return None, {}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, {}
    vectors: dict[str, list[float]] = {}
    for shard_path in out_dir.glob("*.json"):
        if shard_path.name == "_meta.json":
            continue
        try:
            data = json.loads(shard_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rel = data.get("file")
        if not rel:
            continue
        try:
            current = hashlib.sha256(
                (root / rel).read_bytes()).hexdigest()
        except OSError:
            continue
        if current != data.get("file_sha256"):
            continue  # stale source: its vectors are omitted, not trusted
        vectors.update(data.get("vectors", {}))
    return meta, vectors


def retrieve_hybrid(graph: graph_mod.Graph, task: str, root: Path,
                    limit: int = 8) -> list[graph_mod.Symbol]:
    """RRF-fused lexical + vector retrieval, lexical fallback when needed.

    Falls back to plain lexical retrieval when no vector index exists.
    Symbols from stale files keep their lexical scores but get no vector
    score — stale vectors are never trusted.
    """
    lex = graph_mod.lexical_ranked(graph, task)
    meta, vecs = load_vectors(graph, root)
    if not meta or not vecs:
        return graph_mod.retrieve_spans(graph, task, limit)
    idf = meta.get("idf", {})
    task_vec = embed(tokenize(task), idf)
    stale = set(graph_mod.stale_files(graph, root))
    vec_ranked: list[tuple[float, graph_mod.Symbol]] = []
    for sym in graph.symbols:
        if sym.file in stale:
            continue
        vec = vecs.get(_key(sym))
        if vec is None:
            continue
        sim = cosine(task_vec, vec)
        if sim > 0:
            vec_ranked.append((sim, sym))
    vec_ranked.sort(key=lambda item: -item[0])
    ranks: dict[str, dict[str, int]] = {}
    for i, sym in enumerate(lex, start=1):
        ranks.setdefault(_key(sym), {})["lex"] = i
    for i, (_, sym) in enumerate(vec_ranked, start=1):
        ranks.setdefault(_key(sym), {})["vec"] = i
    fused = sorted(
        ((sum(1.0 / (RRF_K + r) for r in entry.values()), key)
         for key, entry in ranks.items()),
        key=lambda item: -item[0],
    )
    by_key = {_key(s): s for s in graph.symbols}
    picked = [by_key[key] for _, key in fused[:limit]]
    # Graph neighbours only after direct results.
    picked_names = {s.name for s in picked}
    for sym in graph.symbols:
        if sym.name in picked_names:
            continue
        if any(sym.name in p.calls for p in picked):
            picked.append(sym)
    return picked[: limit + 4]
