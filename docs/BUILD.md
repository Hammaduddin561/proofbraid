# Proofbraid: Meta-Only Build Doc

Submission target: Meta Global AI Developer Hackathon, Indie Dev Track.
Deadline: Nov 4, 9:00 PM ET = Nov 5, 7:30 AM IST. Our target: Nov 4 evening IST.

This doc is the single source of truth for the build. One partner API is in
play: the Meta Model API. Everything else is out for this submission.

## 1. What Proofbraid is

Proofbraid is a local developer tool that stops AI coding agents from breaking
code. It maps a repo into a real dependency graph from the language parser,
retrieves only structurally related code for a task, asks Meta's Model API
(Muse Spark) to propose a change, and verifies that change (compile, lint,
tests) in a disposable copy before anything touches the real files. Verified
fixes are recorded as hypotheses; older hypotheses on the same files are
evicted so stale claims never linger. Dry run is the default; nothing is
applied without a verified pass plus an explicit flag.

Never describe Proofbraid as RAG. Lead with the dependency graph plus verify
before apply plus hypothesis eviction.

## 2. The loop

```
proofbraid index <repo>                 parse repo -> .proofbraid/graph.json
proofbraid graph <repo>                 print symbols and call edges
proofbraid vectors <repo>               build local hybrid retrieval shards
proofbraid propose <repo> "task"        retrieve -> propose -> verify -> record
proofbraid verify <repo>                run the verification loop directly
proofbraid hypotheses <repo>            list saved and evicted hypotheses
proofbraid providers                    show provider roles and config state
proofbraid daemon <repo> [--once]       reindex on file change
```

`propose` in detail:

1. Retrieve spans: hybrid lexical plus local vector fusion (RRF, k=60).
   Lexical is the proven fallback when no vectors exist.
2. Ask the Meta Model API for new file contents (JSON, full files).
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
  sandbox.py     Modal sandbox shape with local fallback (NOT live tested)
  aux.py         Fireworks one line summaries (skipped when unconfigured)
  daemon.py      mtime polling watcher
examples/sample_repo/
  calc.py        sample with a deliberate off by one in add()
  test_calc.py   its tests (verify FAILS the sample as designed)
tests/test_smoke.py   19 tests, all green
```

Pure Python 3.10+, zero third party dependencies. `.venv` is git ignored.

## 4. The one partner API: Meta Model API

This is the only partner API used in the submission. Everything the demo
claims about models runs through it.

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

Usage:

```bash
export MODEL_API_KEY="..."
proofbraid propose examples/sample_repo "fix the off by one in add()"
proofbraid propose <repo> "task" --apply   # only after a verified pass
```

Without a key, `propose` fails cleanly with instructions. Never hardcode
a key. Never commit a key. Tests must pass with no key set (provider
calls are mocked in the test suite).

## 5. What Meta's other pieces do in this project

| Meta piece | Role in this submission |
|---|---|
| Meta Model API (Muse Spark 1.3) | THE proposer. Every model call in the demo runs through it. The one partner API. |
| Muse Spark Open / Glimmer (open weights) | NOT used. No partner compute is in play for them, so the demo claims no second proposer and no $10K open weights bonus. The provider code paths exist in the repo but stay unconfigured. |
| Muse Code (Meta's terminal coding agent) | NOT a component of Proofbraid. Proofbraid is agent agnostic: it verifies what any coding agent proposes. Its only role is narrative: agents propose, Proofbraid verifies. Do not claim integration. |

Judging gives 25% to creative use of Meta AI tools. That score comes from
the Model API being the core of the propose loop plus a demo that shows it
working, not from name dropping other Meta products.

## 6. What is proven (do not regress)

- 19/19 unit tests pass (`python -m unittest discover -s tests`).
- Full loop proven live Oct 27 on the sample bug (contributor tier).
- Hybrid retrieval implemented and tested; vectors are hashed TF-IDF in
  stdlib, NOT neural embeddings. Docstrings say this plainly.
- `.gitignore` excludes `.venv`, `__pycache__`, `.env`, runtime artifacts.
- No real API keys anywhere in source; only env var references.

## 7. What Junie is building now

One task (see the Junie prompt file, run in Junie CLI from the project
root): unified diff output mode for `propose`, plus a docs truth pass
that brings the README in line with this Meta-only doc. Full file output
stays the default; the proven loop must not break; all 19 tests stay
green; new tests cover the diff mode. Junie runs locally and edits with
approval; it does not need the Meta API key. Tests must pass with no
live key (mocked provider, like the existing suite).

Explicitly NOT in scope: tree-sitter JS/TS indexing (Python only, stated
honestly), Modal sandbox live run, Fireworks summaries, Baseten propose.

## 8. Commands cheat sheet

```bash
.venv/bin/proofbraid index examples/sample_repo
.venv/bin/proofbraid graph examples/sample_repo
.venv/bin/proofbraid verify examples/sample_repo
.venv/bin/proofbraid propose examples/sample_repo "fix the off by one in add()"
.venv/bin/proofbraid hypotheses examples/sample_repo
.venv/bin/proofbraid providers
python -m unittest discover -s tests
```

## 9. Honest limits (never claim these)

- Indexer covers Python only (stdlib ast). No tree-sitter, no JS/TS.
- `propose` returns full file contents by default; diff mode is new.
- Vectors are hashed TF-IDF, not neural embeddings.
- Hybrid retrieval is experimental and unmeasured; lexical is proven.
- Verification runs locally; no live sandbox in this submission.
- One proposer only: Meta Model API. No second proposer claim.
- Demo video shows only Oct 26 to Nov 4 work.

## 10. Submission checklist

- [ ] Jules PR merged, 19+ tests green
- [ ] Repo pushed to GitHub and PUBLIC
- [ ] Repo opens in a logged out browser (verify before submitting)
- [ ] README matches this doc
- [ ] 3 minute demo video recorded (event work only)
- [ ] Submit at https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit
- [ ] Name Indie Dev Track and Meta Model API (Muse Spark) explicitly
- [ ] Demo posted on X (viral prize) after submission
