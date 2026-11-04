"""Smoke tests for the Proofbraid scaffold (stdlib unittest only)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from proofbraid import graph as graph_mod  # noqa: E402
from proofbraid import store  # noqa: E402
from proofbraid import verify as verify_mod  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample_repo"


class TestGraph(unittest.TestCase):
    def test_index_finds_symbols(self):
        graph = graph_mod.index_repo(SAMPLE)
        names = {s.name for s in graph.symbols}
        self.assertIn("add", names)
        self.assertIn("double", names)

    def test_double_calls_add(self):
        graph = graph_mod.index_repo(SAMPLE)
        double = next(s for s in graph.symbols if s.name == "double")
        self.assertIn("add", double.calls)

    def test_retrieve_finds_add_for_task(self):
        graph = graph_mod.index_repo(SAMPLE)
        spans = graph_mod.retrieve_spans(graph, "fix the off-by-one in add()")
        self.assertTrue(any(s.name == "add" for s in spans))


class TestVerify(unittest.TestCase):
    def test_sample_repo_fails_verification(self):
        # The sample repo has a real off-by-one bug, so its tests fail.
        failures = verify_mod.verify_repo(SAMPLE)
        self.assertTrue(any("tests failed" in f for f in failures))


class TestStore(unittest.TestCase):
    def test_eviction_on_verified_success(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.record_hypothesis(root, "t1", ["a.py"], {"a.py": "x"},
                                    verified=True)
            store.record_hypothesis(root, "t2", ["a.py"], {"a.py": "y"},
                                    verified=True)
            hyps = store.load_hypotheses(root)
            self.assertEqual(len(hyps), 1)
            self.assertEqual(hyps[0].task, "t2")


if __name__ == "__main__":
    unittest.main()

import os
from unittest import mock

from proofbraid import aux_calls as aux_mod  # noqa: E402
from proofbraid import meta_client  # noqa: E402
from proofbraid import propose as propose_mod  # noqa: E402
from proofbraid import providers  # noqa: E402
from proofbraid import sandbox as sandbox_mod  # noqa: E402


def _no_keys():
    """Patch the partner key/model env vars to empty for a test."""
    return mock.patch.dict(
        os.environ,
        {k: "" for k in
         ("MODEL_API_KEY", "BASETEN_API_KEY", "FIREWORKS_API_KEY",
          "BASETEN_MODEL", "FIREWORKS_MODEL", "MODAL_SANDBOX")},
        clear=False,
    )


class TestPartners(unittest.TestCase):
    def test_unknown_provider(self):
        with self.assertRaises(providers.ProviderError):
            providers.chat_json("nope", "s", "u")

    def test_missing_keys_are_clean_errors(self):
        with _no_keys():
            for name in ("meta", "baseten", "fireworks"):
                with self.assertRaises(providers.ProviderError):
                    providers.chat_json(name, "s", "u")

    def test_meta_client_missing_key_message(self):
        with _no_keys():
            with self.assertRaises(meta_client.MetaApiError) as ctx:
                meta_client.propose_file_contents("s", "u")
            self.assertIn("MODEL_API_KEY is not set", str(ctx.exception))

    def test_provider_status_lists_presets_and_custom(self):
        rows = providers.provider_status()
        self.assertEqual({r["provider"] for r in rows},
                         {"meta", "baseten", "fireworks", "custom"})

    def test_sandbox_defaults_to_local(self):
        with _no_keys():
            failures, via = sandbox_mod.verify_in_sandbox(SAMPLE)
            self.assertEqual(via, "local")
            self.assertTrue(failures)  # sample repo really fails

    def test_aux_skipped_without_key(self):
        with _no_keys():
            self.assertIsNone(aux_mod.summarize_hypothesis("t", ["a.py"]))

    def test_propose_cascade_reports_each_failure(self):
        with _no_keys():
            report = propose_mod.run_propose(SAMPLE, "fix add", provider="all")
            self.assertFalse(report["ok"])
            self.assertEqual(report["error"], "all providers failed")
            self.assertEqual(len(report["provider_attempts"]), 3)

class TestUsage(unittest.TestCase):
    def test_usage_captured_from_response(self):
        import json as json_mod
        import urllib.request

        body = json_mod.dumps({
            "choices": [{"message": {"content": json_mod.dumps({"files": []})}}],
            "usage": {"prompt_tokens": 281, "completion_tokens": 190,
                      "total_tokens": 471},
        }).encode("utf-8")

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return body

        with mock.patch.dict(os.environ, {"MODEL_API_KEY": "x",
                                          "PROOFBRAID_MODEL": "m"}):
            with mock.patch.object(urllib.request, "urlopen",
                                   return_value=Resp()):
                out = providers.chat_json("meta", "s", "u")
        self.assertEqual(out, {"files": []})
        self.assertEqual(providers.LAST_USAGE.get("total_tokens"), 471)

from proofbraid import vectors as vectors_mod  # noqa: E402


class TestHybrid(unittest.TestCase):
    def test_cosine_and_embed(self):
        idf = {"add": 1.0, "double": 1.0}
        a = vectors_mod.embed(["add"], idf)
        b = vectors_mod.embed(["add"], idf)
        c = vectors_mod.embed(["double"], idf)
        self.assertAlmostEqual(vectors_mod.cosine(a, b), 1.0, places=5)
        self.assertLess(vectors_mod.cosine(a, c), 0.99)

    def test_hybrid_finds_add_and_falls_back(self):
        graph = graph_mod.index_repo(SAMPLE)
        vectors_mod.build_vectors(graph)
        try:
            names = {s.name for s in vectors_mod.retrieve_hybrid(
                graph, "fix the off-by-one in add()", SAMPLE)}
            self.assertIn("add", names)
        finally:
            import shutil
            shutil.rmtree(vectors_mod.vectors_dir(SAMPLE), ignore_errors=True)
        self.assertEqual(
            [s.name for s in vectors_mod.retrieve_hybrid(
                graph, "fix the off-by-one in add()", SAMPLE)],
            [s.name for s in graph_mod.retrieve_spans(
                graph, "fix the off-by-one in add()")],
        )

    def test_stale_vectors_omitted(self):
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            shutil.copytree(SAMPLE, repo,
                            ignore=shutil.ignore_patterns(".proofbraid"))
            graph = graph_mod.index_repo(repo)
            vectors_mod.build_vectors(graph)
            _, vecs = vectors_mod.load_vectors(graph, repo)
            self.assertTrue(vecs)
            (repo / "calc.py").write_text(
                (repo / "calc.py").read_text(encoding="utf-8") + "\n# tweak\n",
                encoding="utf-8")
            _, vecs2 = vectors_mod.load_vectors(graph, repo)
            self.assertFalse(any(k.startswith("calc.py:") for k in vecs2))


class TestProvenance(unittest.TestCase):
    def test_non_git_repo_has_no_provenance(self):
        graph = graph_mod.index_repo(SAMPLE)
        self.assertIsNone(graph.git_head)
        self.assertEqual(graph.git_revs, {})

    def test_symbol_end_lines_recorded(self):
        graph = graph_mod.index_repo(SAMPLE)
        add = next(s for s in graph.symbols if s.name == "add")
        self.assertGreater(add.end_line, add.line)

    def test_git_repo_provenance(self):
        import shutil
        import subprocess
        import tempfile

        if shutil.which("git") is None:
            self.skipTest("git not available")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            shutil.copytree(SAMPLE, repo,
                            ignore=shutil.ignore_patterns(".proofbraid"))
            subprocess.run(["git", "init"], cwd=repo, capture_output=True,
                           check=True)
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True,
                           check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-m", "init"], cwd=repo, capture_output=True,
                check=True)
            graph = graph_mod.index_repo(repo)
            self.assertIsNotNone(graph.git_head)
            self.assertEqual(len(graph.git_head), 40)
            self.assertIn("calc.py", graph.git_revs)
