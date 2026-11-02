"""Tests for unified diff proposals without live provider calls."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from proofbraid import providers, propose as propose_mod, store  # noqa: E402
from proofbraid.__main__ import build_parser  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "examples" / "sample_repo"
VALID_DIFF = (
    "--- a/calc.py\n"
    "+++ b/calc.py\n"
    "@@ -12 +12 @@\n"
    "-    return a + b + 1  # BUG: off by one\n"
    "+    return a + b\n"
)


def _no_keys():
    return mock.patch.dict(
        os.environ,
        {key: "" for key in (
            "MODEL_API_KEY", "PROOFBRAID_MODEL", "BASETEN_API_KEY",
            "BASETEN_MODEL", "FIREWORKS_API_KEY", "FIREWORKS_MODEL",
        )},
        clear=False,
    )


class TestDiffMode(unittest.TestCase):
    def _copy_sample(self) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name) / "repo"
        shutil.copytree(SAMPLE, repo, ignore=shutil.ignore_patterns(".proofbraid"))
        return repo

    def _proposal(self, diff=VALID_DIFF, path="calc.py"):
        return {"files": [{"path": path, "diff": diff}]}

    def _run_diff(self, repo, response=None, **kwargs):
        with _no_keys(), mock.patch.object(
                propose_mod.providers, "chat_json",
                return_value=response or self._proposal()):
            return propose_mod.run_propose(repo, "fix the off by one in add()",
                                           diff=True, **kwargs)

    def test_valid_diff_verifies_dry_run_and_records_hypothesis(self):
        repo = self._copy_sample()
        original = (repo / "calc.py").read_bytes()

        report = self._run_diff(repo)

        self.assertTrue(report["ok"])
        self.assertTrue(report["verified"])
        self.assertEqual(report["mode"], "diff")
        self.assertEqual(report["files"], ["calc.py"])
        self.assertEqual((repo / "calc.py").read_bytes(), original)
        hypotheses = store.load_hypotheses(repo)
        self.assertEqual(len(hypotheses), 1)
        self.assertTrue(hypotheses[0].verified)

    def test_valid_diff_applies_only_with_apply_flag(self):
        repo = self._copy_sample()

        report = self._run_diff(repo, apply=True)

        self.assertTrue(report["ok"])
        self.assertTrue(report["applied"])
        self.assertIn("return a + b\n", (repo / "calc.py").read_text())
        self.assertNotIn("return a + b + 1", (repo / "calc.py").read_text())

    def test_bad_hunk_header_fails_without_recording_or_writing(self):
        repo = self._copy_sample()
        original = (repo / "calc.py").read_bytes()
        bad_diff = "--- a/calc.py\n+++ b/calc.py\n@@ not a hunk @@\n"

        report = self._run_diff(repo, self._proposal(bad_diff))

        self.assertFalse(report["ok"])
        self.assertTrue(report["error"].startswith("malformed diff for calc.py:"))
        self.assertEqual((repo / "calc.py").read_bytes(), original)
        self.assertEqual(store.load_hypotheses(repo), [])

    def test_context_mismatch_fails_cleanly(self):
        repo = self._copy_sample()
        original = (repo / "calc.py").read_bytes()
        bad_diff = VALID_DIFF.replace("return a + b + 1", "return a + b + 2")

        report = self._run_diff(repo, self._proposal(bad_diff))

        self.assertFalse(report["ok"])
        self.assertIn("removed line does not match", report["error"])
        self.assertEqual((repo / "calc.py").read_bytes(), original)
        self.assertEqual(store.load_hypotheses(repo), [])

    def test_mismatched_hunk_counts_fail(self):
        diff = VALID_DIFF.replace("@@ -12 +12 @@", "@@ -12,2 +12,2 @@")
        with self.assertRaisesRegex(ValueError, "line counts"):
            propose_mod.apply_unified_diff((SAMPLE / "calc.py").read_text(), diff)

    def test_multiple_hunks_preserve_crlf_and_trailing_newline(self):
        original = "one\r\nold\r\nmiddle\r\nlast\r\n"
        diff = (
            "--- a/file.py\r\n+++ b/file.py\r\n"
            "@@ -2 +2 @@\r\n-old\r\n+new\r\n"
            "@@ -4 +4 @@\r\n-last\r\n+end\r\n")

        self.assertEqual(
            propose_mod.apply_unified_diff(original, diff),
            "one\r\nnew\r\nmiddle\r\nend\r\n")

    def test_unsafe_path_fails_before_hypothesis_recording(self):
        repo = self._copy_sample()

        report = self._run_diff(repo, self._proposal(path="../escape.py"))

        self.assertFalse(report["ok"])
        self.assertTrue(report["error"].startswith("malformed diff for ../escape.py:"))
        self.assertEqual(store.load_hypotheses(repo), [])

    def test_multiple_files_are_validated_before_any_diff_write(self):
        repo = self._copy_sample()
        bad_diff = "--- a/test_calc.py\n+++ b/test_calc.py\n@@ bad @@\n"
        response = {"files": [
            {"path": "calc.py", "diff": VALID_DIFF},
            {"path": "test_calc.py", "diff": bad_diff},
        ]}

        with _no_keys(), mock.patch.object(
                propose_mod.providers, "chat_json", return_value=response), \
                mock.patch.object(Path, "write_bytes", autospec=True) as writes:
            report = propose_mod.run_propose(
                repo, "fix the off by one in add()", diff=True)

        self.assertFalse(report["ok"])
        self.assertIn("malformed diff for test_calc.py:", report["error"])
        writes.assert_not_called()
        self.assertEqual(store.load_hypotheses(repo), [])

    def test_full_file_mode_remains_default_and_uses_original_prompt(self):
        repo = self._copy_sample()
        content = (repo / "calc.py").read_text().replace(
            "return a + b + 1  # BUG: off by one", "return a + b")
        with _no_keys(), mock.patch.object(
                propose_mod.providers, "chat_json",
                return_value={"files": [{"path": "calc.py", "content": content}]}) as chat:
            report = propose_mod.run_propose(repo, "fix add")

        self.assertTrue(report["ok"])
        self.assertTrue(report["verified"])
        self.assertEqual(report["mode"], "full")
        self.assertEqual(chat.call_args_list[0].args[:2],
                         ("meta", propose_mod.SYSTEM_PROMPT))

    def test_baseten_diff_mode_uses_diff_prompt(self):
        repo = self._copy_sample()
        with _no_keys(), mock.patch.object(
                propose_mod.providers, "chat_json",
                return_value=self._proposal()) as chat:
            report = propose_mod.run_propose(
                repo, "fix add", provider="baseten", diff=True)

        self.assertTrue(report["ok"])
        self.assertEqual(report["provider"], "baseten")
        self.assertEqual(chat.call_args_list[0].args[:2],
                         ("baseten", propose_mod.DIFF_SYSTEM_PROMPT))

    def test_diff_mode_uses_existing_provider_cascade(self):
        repo = self._copy_sample()

        def respond(provider, system, prompt):
            if provider == "meta":
                raise providers.ProviderError("meta unavailable")
            return self._proposal()

        with _no_keys(), mock.patch.object(
                propose_mod.providers, "chat_json", side_effect=respond) as chat:
            report = propose_mod.run_propose(
                repo, "fix add", provider="all", diff=True)

        self.assertTrue(report["ok"])
        self.assertEqual(report["provider"], "baseten")
        self.assertEqual([call.args[0] for call in chat.call_args_list[:2]],
                         ["meta", "baseten"])
        self.assertEqual(report["provider_attempts"], [
            {"provider": "meta", "error": "meta unavailable"},
        ])

    def test_applier_accepts_new_file_and_rejects_deletion(self):
        new_file_diff = (
            "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+value = 1\n")
        self.assertEqual(propose_mod.apply_unified_diff("", new_file_diff),
                         "value = 1")
        deletion = "--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-value = 1\n"
        with self.assertRaisesRegex(ValueError, "deletion"):
            propose_mod.apply_unified_diff("value = 1\n", deletion)

    def test_cli_accepts_diff_flag(self):
        args = build_parser().parse_args(["propose", "repo", "fix add", "--diff"])
        self.assertTrue(args.diff)


if __name__ == "__main__":
    unittest.main()
