"""Proofbraid CLI.

Usage:
    python3 -m proofbraid index <repo>
    python3 -m proofbraid graph <repo>
    python3 -m proofbraid verify <repo>
    python3 -m proofbraid propose <repo> "task description" [--diff] [--apply]
    python3 -m proofbraid daemon <repo> [--interval SECONDS] [--once]
    python3 -m proofbraid hypotheses <repo>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofbraid import daemon as daemon_mod  # noqa: E402
from proofbraid import graph as graph_mod  # noqa: E402
from proofbraid import propose as propose_mod  # noqa: E402
from proofbraid import providers as providers_mod  # noqa: E402
from proofbraid import store  # noqa: E402
from proofbraid import verify as verify_mod  # noqa: E402


def _cmd_index(args) -> int:
    graph = graph_mod.index_repo(Path(args.repo))
    path = graph_mod.save_graph(graph)
    print(f"indexed {len(graph.files)} files, {len(graph.symbols)} symbols -> {path}")
    if graph.git_head:
        print(f"git HEAD: {graph.git_head[:12]}")
    try:
        from proofbraid import vectors as vectors_mod  # noqa: E402
        n_sym, n_files = vectors_mod.build_vectors(graph)
        print(f"vectors: {n_sym} symbols in {n_files} shards")
    except Exception as exc:
        print(f"vectors skipped: {exc}", file=sys.stderr)
    return 0


def _cmd_graph(args) -> int:
    graph = graph_mod.load_graph(Path(args.repo))
    if graph is None:
        print("no graph yet; run `proofbraid index` first", file=sys.stderr)
        return 1
    for sym in graph.symbols:
        print(f"{sym.kind:8} {sym.name}  {sym.file}:{sym.line}"
              f"  calls: {', '.join(sym.calls) or '-'}")
    return 0


def _cmd_verify(args) -> int:
    failures = verify_mod.verify_repo(Path(args.repo))
    if failures:
        print("VERIFICATION FAILED:")
        for failure in failures:
            print(f"---\n{failure}")
        return 1
    print("verified: no failures")
    return 0


def _cmd_propose(args) -> int:
    report = propose_mod.run_propose(
        Path(args.repo), args.task, apply=args.apply,
        provider=args.provider, sandbox=args.sandbox, diff=args.diff,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


def _cmd_daemon(args) -> int:
    daemon_mod.watch(Path(args.repo), interval=args.interval, once=args.once)
    return 0


def _cmd_hypotheses(args) -> int:
    for hyp in store.load_hypotheses(Path(args.repo)):
        status = "verified" if hyp.verified else "FAILED"
        print(f"{hyp.id} [{status}] {hyp.task} :: {', '.join(hyp.files)}")
    return 0


def _cmd_providers(args) -> int:
    for row in providers_mod.provider_status():
        state = "ready" if row["ready"] else "not configured"
        print(f"{row['provider']:10} [{state}] {row['role']}")
        print(f"           key: {row['key_env']} "
              f"({'set' if row['key_set'] else 'missing'}); "
              f"model: {row['model']}")
    return 0


def _cmd_vectors(args) -> int:
    from proofbraid import vectors as vectors_mod  # noqa: E402
    graph = graph_mod.load_graph(Path(args.repo)) or graph_mod.index_repo(
        Path(args.repo))
    n_sym, n_files = vectors_mod.build_vectors(graph)
    print(f"vectors: {n_sym} symbols in {n_files} shards "
          f"(hashed TF-IDF, stdlib — not neural embeddings)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proofbraid")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index")
    p.add_argument("repo")
    p.set_defaults(func=_cmd_index)

    p = sub.add_parser("graph")
    p.add_argument("repo")
    p.set_defaults(func=_cmd_graph)

    p = sub.add_parser("verify")
    p.add_argument("repo")
    p.set_defaults(func=_cmd_verify)

    p = sub.add_parser("propose")
    p.add_argument("repo")
    p.add_argument("task")
    p.add_argument("--diff", action="store_true",
                   help="request and apply strict unified diffs")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--provider", default="meta",
                   choices=["meta", "baseten", "fireworks", "all"],
                   help="meta=primary, baseten=open-weights, "
                        "fireworks=aux-capable, all=cascade in that order")
    p.add_argument("--sandbox", default="local", choices=["local", "modal"],
                   help="modal runs verification in a Modal sandbox, "
                        "falling back to local when unconfigured")
    p.set_defaults(func=_cmd_propose)

    p = sub.add_parser("daemon")
    p.add_argument("repo")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--once", action="store_true")
    p.set_defaults(func=_cmd_daemon)

    p = sub.add_parser("hypotheses")
    p.add_argument("repo")
    p.set_defaults(func=_cmd_hypotheses)

    p = sub.add_parser("providers",
                       help="show each partner provider's role and config status")
    p.set_defaults(func=_cmd_providers)

    p = sub.add_parser("vectors",
                       help="build the hybrid-retrieval vector shards for a repo")
    p.add_argument("repo")
    p.set_defaults(func=_cmd_vectors)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
