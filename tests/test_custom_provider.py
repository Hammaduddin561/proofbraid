"""Tests for the custom OpenAI compatible provider (mocked HTTP, no keys)."""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from proofbraid import propose as propose_mod, providers, store  # noqa: E402
from proofbraid.__main__ import main  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample_repo"
CUSTOM_ENV = {
    "PROOFBRAID_BASE_URL": "http://localhost:11434/v1/",
    "PROOFBRAID_API_KEY": "fake-test-value",
    "PROOFBRAID_MODEL": "fake-model",
    "FIREWORKS_API_KEY": "",
}


class _Resp:
    def __init__(self, content):
        self.body = json.dumps(
            {"choices": [{"message": {"content": json.dumps(content)}}]}
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class TestCustomProvider(unittest.TestCase):
    def test_given_all_vars_when_propose_custom_then_targets_custom_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            shutil.copytree(SAMPLE, repo,
                            ignore=shutil.ignore_patterns(".proofbraid"))
            fixed = (repo / "calc.py").read_text().replace(
                "return a + b + 1  # BUG: off by one", "return a + b")
            with mock.patch.dict(os.environ, CUSTOM_ENV), \
                    mock.patch.object(urllib.request, "urlopen",
                                      return_value=_Resp({"files": [
                                          {"path": "calc.py", "content": fixed}]})
                                      ) as urlopen:
                report = propose_mod.run_propose(repo, "fix add",
                                                 provider="custom")
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["provider"], "custom")
            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url,
                             "http://localhost:11434/v1/chat/completions")
            self.assertEqual(json.loads(request.data)["model"], "fake-model")
            self.assertTrue(store.load_hypotheses(repo)[0].verified)

    def test_given_one_var_missing_when_called_then_names_it_without_network(self):
        for missing in ("PROOFBRAID_BASE_URL", "PROOFBRAID_API_KEY",
                        "PROOFBRAID_MODEL"):
            env = {**CUSTOM_ENV, missing: ""}
            with self.subTest(missing=missing), \
                    mock.patch.dict(os.environ, env), \
                    mock.patch.object(urllib.request, "urlopen") as urlopen:
                with self.assertRaises(providers.ProviderError) as ctx:
                    providers.chat_json("custom", "s", "u")
                self.assertIn(missing, str(ctx.exception).split(" not set")[0])
                report = propose_mod.run_propose(SAMPLE, "fix add",
                                                 provider="custom")
                self.assertFalse(report["ok"])
                self.assertIn(missing, report["provider_attempts"][0]["error"])
                urlopen.assert_not_called()

    def _providers_output(self, env):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            main(["providers"])
        return next(line for line in out.getvalue().splitlines()
                    if line.startswith("custom"))

    def test_providers_shows_custom_configured_state(self):
        self.assertIn("[ready]", self._providers_output(CUSTOM_ENV))
        absent = {k: "" for k in CUSTOM_ENV}
        self.assertIn("[not configured]", self._providers_output(absent))


if __name__ == "__main__":
    unittest.main()
