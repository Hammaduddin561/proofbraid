"""Background daemon: re-index the repo when files change.

Polling mtimes (portable, no platform watcher dependency). On every
pass it compares content hashes against the saved graph; if anything
changed, it re-indexes and prints what moved. This is what keeps the
graph "real-time" in the hackathon proposal: the agent never reasons
over a stale map of the repo.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import graph as graph_mod


def watch(root: Path, interval: float = 2.0, once: bool = False) -> None:
    root = root.resolve()
    graph = graph_mod.load_graph(root) or graph_mod.index_repo(root)
    graph_mod.save_graph(graph)
    print(f"proofbraid daemon watching {root} (interval {interval}s, Ctrl-C to stop)")
    try:
        while True:
            stale = graph_mod.stale_files(graph, root)
            if stale:
                print(f"change detected: {', '.join(stale)} — re-indexing")
                graph = graph_mod.index_repo(root)
                graph_mod.save_graph(graph)
                print(f"graph now covers {len(graph.files)} files, "
                      f"{len(graph.symbols)} symbols")
            if once:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nproofbraid daemon stopped")
