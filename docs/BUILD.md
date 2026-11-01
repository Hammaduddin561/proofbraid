# Proofbraid: Build Doc

Submission target: Meta Global AI Developer Hackathon, Indie Dev Track.
Deadline: Nov 4, 9:00 PM ET = Nov 5, 7:30 AM IST. Our target: Nov 4 evening IST.

This doc is the single source of truth for the build. Two partner APIs are
in play: the Meta Model API (primary proposer) and Baseten (open weights
second proposer). Everything else is out for this submission.

## 1. What Proofbraid is

Proofbraid is a local developer tool that stops AI coding agents from
breaking code. It maps a repo into a real dependency graph from the
language parser, retrieves only structurally related code for a task, asks
a model to propose a change, and verifies that change (compile, lint,
tests) in a disposable copy before anything touches the real files.
Verified fixes are recorded as hypotheses; older hypotheses on the same
files are evicted so stale claims never linger. Dry run is the default;
nothing is applied without a verified pass plus an explicit flag.

Never describe Proofbraid as RAG. Lead with the dependency graph plus
verify before apply plus hypothesis eviction.

## 2. The loop

```
proofbraid index <repo>                 parse repo -> .proofbraid/graph.json
proofbraid graph <repo>                 print symbols and call edges
proofbraid vectors <repo>               build local hybrid retrieval shards
proofbraid propose <repo> "task" [--provider meta|baseten] [--diff] [--apply]
proofbraid verify <repo>                run the verification loop directly
proofbraid hypotheses <repo>            list saved and evicted hypotheses
proofbraid providers                    show provider roles and config state
proofbraid daemon <repo> [--once]       reindex on file change
```

`propose` in detail:

1. Retrieve spans: hybrid lexical plus local vector fusion (RRF, k=60).
   Lexical is the proven fallback when no vectors exist.
2. Ask the chosen provider for new file contents (JSON, full files).
3. Write the proposal into a disposable temp copy of the repo.
4. Run verification on the temp copy: py_compile, optional linter,
   pytest or unittest when tests exist.
5. On PASS: save the hypothesis, evict superseded ones, apply only with
   --apply. On FAIL: report reasons, real repo untouched.

## 3. Repo map

```
src/proofbraid/
  __main__.py    CLI wiring for every command above
  graph.py       stdlib ast indexer: functions, classes, methods, imports,
                 call edges, SHA-256 per file, git provenance, stale check
  vectors.py     hashed TF-IDF vectors (256 dims, stdlib only), per file
                 shards under .proofbraid/vectors, RRF fusion with lexical
  store.py       hypotheses in .proofbraid/hypotheses.json, eviction logic
  verify.py      py_compile plus linter plus pytest/unittest discovery
  meta_client.py Meta Model API client (OpenAI compatible shape)
  providers.py   one generic OpenAI compatible client for every provider,
                 usage capture in LAST_USAGE
  propose.py     the retrieve -> prompt -> verify -> record pipeline
  sandbox.py     Modal sandbox shape with local fallback (NOT live tested,
                 NOT used in this submission)
  aux.py         Fireworks one line summaries (skipped when unconfigured,
                 NOT used in this submission)
  daemon.py      mtime polling watcher
examples/sample_repo/
  calc.py        sample with a deliberate off by one in add()
  test_calc.py   its tests (verify FAILS the sample as designed)
tests/test_smoke.py   19 tests, all green
```

Pure Python 3.10+, zero third party dependencies. `.venv` is git ignored.
License: MIT (see LICENSE).

## 4. Partner API one: Meta Model API (primary proposer)

The default provider. Every demo propose shot runs through it unless
stated otherwise.

- Base URL: `https://api.meta.ai/v1`
- Auth: `Authorization: Bearer $MODEL_API_KEY`
- Endpoint: POST `/v1/chat/completions` (OpenAI compatible)
- Default model: `muse-spark-1.3`
- Cheap tier: `muse-spark-1.3-contributor` ($0.10 in / $0.20 out per 1M
  tokens; Meta may train on this tier, so keep private code on Standard)
- Standard tier: $1.25 in / $4.25 out per 1M tokens
- Credits: $150 claimed Oct 27, 2026. Key obtained same day. Claim
  deadline Nov 4, 9:00 PM ET. A card must be on file to call the API.
- Health: `https://api.meta.ai/v1/status` (no key needed)
- Proven live Oct 27: `propose` on the sample repo with the contributor
  model returned ok:true, verified:true, hypothesis recorded, dry run.

```bash
export MODEL_API_KEY="..."
proofbraid propose examples/sample_repo "fix the off by one in add()"
proofbraid propose <repo> "task" --apply   # only after a verified pass
```

## 5. Partner API two: Baseten (open weights second proposer)

The second proposer, on Meta open weights. This is also the path to the
$10K Best Build on Meta Open Weights bonus.

- Base URL: `https://inference.baseten.co/v1` (OpenAI compatible)
- Auth: `Authorization: Bearer $BASETEN_API_KEY`
- Model: EXACTLY `meta-models/muse-spark-open-1026` (Muse Spark Open).
  The docs shorthand without the version suffix does NOT work with this key.
- Credits: $100 + 2M TPM, selection email arrived Oct 29, 2026. Md saved
  the key himself; it is never in chat, never stored, never in the repo.
- Model notes: reasoning is always on (reasoning_effort low/medium/high/
  xhigh); no enforced JSON mode, so ask for JSON in the prompt and parse
  defensively; `n` must be 1; reasoning tokens count toward billing.

```bash
export BASETEN_API_KEY="..." BASETEN_MODEL="meta-models/muse-spark-open-1026"
proofbraid propose examples/sample_repo "fix the off by one in add()" --provider baseten
```

Status: the provider path is wired and unit tested, but the LIVE Baseten
propose has not run yet. It needs Md's key at runtime for a one time run.
His call. Until that run succeeds, the demo must NOT claim a second
proposer and the bonus stays unclaimed. The moment it succeeds, both the
demo claim and the bonus path are live.

Without any key, `propose --provider baseten` fails cleanly with
instructions. Never hardcode a key. Never commit a key. Tests must pass
with no keys set (provider calls are mocked in the test suite).

## 6. What Meta's other pieces do in this project

| Meta piece | Role in this submission |
|---|---|
| Meta Model API (Muse Spark 1.3) | Primary proposer. Default for every propose call. |
| Baseten hosted Muse Spark Open | Second proposer on open weights. Live run pending Md's key. Bonus path when proven. |
| Muse Glimmer | Not used. No compute in play for it. |
| Muse Code (Meta's terminal coding agent) | NOT a component of Proofbraid. Proofbraid is agent agnostic: it verifies what any coding agent proposes. Narrative role only: agents propose, Proofbraid verifies. Do not claim integration. |
| Modal / Fireworks | Out. Code paths exist but stay unconfigured and unclaimed. |

Judging gives 25% to creative use of Meta AI tools. That score comes from
the Model API being the core of the propose loop, plus the open weights
second proposer when the live run lands, plus a demo that shows both
working. Not from name dropping other products.

## 7. What is proven (do not regress)

- 19/19 unit tests pass (`python -m unittest discover -s tests`).
- Full loop proven live Oct 27 on the sample bug (Meta contributor tier).
- Hybrid retrieval implemented and tested; vectors are hashed TF-IDF in
  stdlib, NOT neural embeddings. Docstrings say this plainly.
- `.gitignore` excludes `.venv`, `__pycache__`, `.env`, runtime artifacts.
- No real API keys anywhere in source; only env var references.

## 8. What Junie is building now

One task (see the Junie prompt file, run in Junie CLI from the project
root): unified diff output mode for `propose`, plus a docs truth pass
that brings the README in line with this doc. Full file output stays the
default; the proven loop must not break; all 19 tests stay green; new
tests cover the diff mode. Junie runs locally and edits with approval; it
does not need any API key. Tests must pass with no live keys (mocked
provider, like the existing suite). The README truth pass must describe
BOTH providers: Meta primary, Baseten second proposer (live run pending).

Explicitly NOT in scope: tree-sitter JS/TS indexing (Python only, stated
honestly), Modal sandbox live run, Fireworks summaries.

## 9. Commands cheat sheet

```bash
.venv/bin/proofbraid index examples/sample_repo
.venv/bin/proofbraid graph examples/sample_repo
.venv/bin/proofbraid verify examples/sample_repo
.venv/bin/proofbraid propose examples/sample_repo "fix the off by one in add()"
.venv/bin/proofbraid propose <repo> "task" --provider baseten
.venv/bin/proofbraid hypotheses examples/sample_repo
.venv/bin/proofbraid providers
python -m unittest discover -s tests
```

## 10. Honest limits (never claim these)

- Indexer covers Python only (stdlib ast). No tree-sitter, no JS/TS.
- `propose` returns full file contents by default; diff mode is new.
- Vectors are hashed TF-IDF, not neural embeddings.
- Hybrid retrieval is experimental and unmeasured; lexical is proven.
- Verification runs locally; no live sandbox in this submission.
- Second proposer (Baseten) claimable ONLY after the live propose run
  succeeds with Md's key. Until then, one proposer: Meta Model API.
- Demo video shows only Oct 26 to Nov 4 work.

## 11. Submission checklist

- [ ] Junie task done, 19+ tests green, merged
- [ ] Live Baseten propose run with Md's key (his call) — decides the
  second proposer claim and the $10K bonus path
- [ ] Repo pushed to GitHub and PUBLIC
- [ ] Repo opens in a logged out browser (verify before submitting)
- [ ] README matches this doc (both providers, honest limits)
- [ ] 3 minute demo video recorded (event work only)
- [ ] Submit at https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit
- [ ] Name Indie Dev Track, Meta Model API (Muse Spark), and Baseten
  hosted Muse Spark Open explicitly (second only if the live run passed)
- [ ] Demo posted on X (viral prize) after submission
