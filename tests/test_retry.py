"""Tests for the propose self-correction retry loop (mocked provider)."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from proofbraid import propose as propose_mod, store  # noqa: E402
from proofbraid.__main__ import build_parser  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample_repo"
BUG = "return a + b + 1  # BUG: off by one"
BAD_DIFF = (
    "--- a/calc.py\n+++ b/calc.py\n@@ -12 +12 @@\n"
    f"-    {BUG}\n+    return a + b + 2\n"
)
GOOD_DIFF = (
    "--- a/calc.py\n+++ b/calc.py\n@@ -12 +12 @@\n"
    f"-    {BUG}\n+    return a + b\n"
)


class TestRetry(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name) / "repo"
        shutil.copytree(SAMPLE, self.repo,
                        ignore=shutil.ignore_patterns(".proofbraid"))
        self.source = (self.repo / "calc.py").read_text()

    def _full(self, replacement):
        return {"files": [{"path": "calc.py",
                           "content": self.source.replace(BUG, replacement)}]}

    def _run(self, responses, **kwargs):
        with mock.patch.object(propose_mod.providers, "chat_json",
                               side_effect=responses) as chat, \
                mock.patch.object(propose_mod.aux_mod, "summarize_hypothesis",
                                  return_value=None):
            report = propose_mod.run_propose(self.repo, "fix add", **kwargs)
        return report, chat

    def test_given_broken_then_fixed_when_retry_2_then_verifies_fixed(self):
        report, chat = self._run(
            [self._full("return a + b + 2"), self._full("return a + b")],
            retry=2, apply=True)

        self.assertTrue(report["ok"])
        self.assertEqual(report["attempts_used"], 2)
        self.assertEqual([a["ok"] for a in report["attempts"]], [False, True])
        self.assertIn("tests failed", report["attempts"][0]["error"])
        self.assertIn("return a + b\n", (self.repo / "calc.py").read_text())
        # Retry goes to the same provider with the failure fed back.
        self.assertEqual([c.args[0] for c in chat.call_args_list],
                         ["meta", "meta"])
        retry_prompt = chat.call_args_list[1].args[2]
        self.assertIn("FAILED VERIFICATION", retry_prompt)
        self.assertIn("return a + b + 2", retry_prompt)
        hyps = store.load_hypotheses(self.repo)
        self.assertEqual([(h.verified, h.attempts_used) for h in hyps],
                         [(True, 2)])

    def test_given_always_broken_when_retry_3_then_fails_without_apply(self):
        report, chat = self._run([self._full("return a + b + 2")] * 3,
                                 retry=3, apply=True)

        self.assertFalse(report["ok"])
        self.assertEqual(report["attempts_used"], 3)
        self.assertEqual(chat.call_count, 3)
        self.assertNotIn("applied", report)
        self.assertEqual((self.repo / "calc.py").read_text(), self.source)
        hyps = store.load_hypotheses(self.repo)
        self.assertEqual(len(hyps), 1)
        self.assertFalse(hyps[0].verified)
        self.assertEqual(hyps[0].attempts_used, 3)
        self.assertEqual(hyps[0].failures, report["failures"])

    def test_given_no_retry_flag_when_broken_then_single_attempt(self):
        report, chat = self._run([self._full("return a + b + 2")] * 3)

        self.assertFalse(report["ok"])
        self.assertFalse(report["verified"])
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(report["attempts_used"], 1)
        self.assertEqual(len(store.load_hypotheses(self.repo)), 1)
        self.assertEqual(build_parser().parse_args(
            ["propose", "r", "t"]).retry, 1)

    def test_given_bad_then_good_diff_when_retry_then_verifies_in_diff_mode(self):
        report, chat = self._run(
            [{"files": [{"path": "calc.py", "diff": BAD_DIFF}]},
             {"files": [{"path": "calc.py", "diff": GOOD_DIFF}]}],
            retry=3, diff=True)

        self.assertTrue(report["ok"])
        self.assertEqual(report["mode"], "diff")
        self.assertEqual(report["attempts_used"], 2)
        self.assertEqual(chat.call_args_list[1].args[1],
                         propose_mod.DIFF_SYSTEM_PROMPT)
        self.assertIn("unified diffs", chat.call_args_list[1].args[2])
        self.assertIn("return a + b + 2", chat.call_args_list[1].args[2])
        self.assertEqual((self.repo / "calc.py").read_text(), self.source)

    def test_retry_is_clamped_to_five(self):
        report, chat = self._run([self._full("return a + b + 2")] * 9,
                                 retry=50)
        self.assertEqual(chat.call_count, 5)
        self.assertEqual(report["attempts_used"], 5)

    def test_given_new_conftest_when_retry_then_rejects_it_and_recovers(self):
        unsafe = self._full("return a + b")
        unsafe["files"].append({"path": "conftest.py", "content": ""})
        report, _ = self._run([unsafe, self._full("return a + b")],
                              retry=2, apply=True)

        self.assertTrue(report["ok"])
        self.assertEqual(report["attempts_used"], 2)
        self.assertIn("conftest.py", report["attempts"][0]["error"])
        self.assertFalse((self.repo / "conftest.py").exists())
        self.assertIn("return a + b\n", (self.repo / "calc.py").read_text())

    def test_given_existing_conftest_when_updated_then_proposal_is_allowed(self):
        conftest = self.repo / "conftest.py"
        conftest.write_text("# existing config\n")
        proposal = self._full("return a + b")
        proposal["files"].append({"path": "conftest.py",
                                   "content": "# updated config\n"})

        report, _ = self._run([proposal])

        self.assertTrue(report["ok"], report)

    def test_given_no_retrieval_matches_when_proposing_then_prompt_includes_code(self):
        with mock.patch.object(propose_mod.vectors_mod, "retrieve_hybrid",
                               return_value=[]), \
                mock.patch.object(propose_mod.providers, "chat_json",
                                  return_value=self._full("return a + b")) as chat, \
                mock.patch.object(propose_mod.aux_mod,
                                  "summarize_hypothesis", return_value=None):
            report = propose_mod.run_propose(
                self.repo, "unrelated request with no matching words")

        self.assertTrue(report["ok"], report)
        prompt = chat.call_args.args[2]
        self.assertIn("calc.py", prompt)
        self.assertIn("def add", prompt)


if __name__ == "__main__":
    unittest.main()
