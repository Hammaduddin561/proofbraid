# Proofbraid — a local, self-correcting coding agent

Proofbraid is the hackathon build submitted for the Meta Global AI Developer
Hackathon (Indie Dev Track): a lightweight, local developer tool that fixes
**context degradation and hallucinations in autonomous coding agents** during
long, multi-file tasks.

## The idea in one paragraph

Standard vector retrieval dumps outdated syntax and broken function
interfaces into the prompt, so coding agents confidently apply changes that
don't fit the repo. Proofbraid instead maps the repo's real-time AST dependency
graph, retrieves only structurally related code spans, asks Meta's Model API
(Muse Spark) to propose a change, and then — before anything touches the real
repo — runs a **pre-execution verification loop** (linters, compilers, test
suites) against the proposed change in a disposable copy. Verified hypotheses
are saved; superseded ones are evicted from memory. The goal: a dependable
coding agent for solo developers that self-corrects without blowing up the
context window or needing massive cloud infrastructure.

## Architecture

```
proofbraid index    <repo>     -> .proofbraid/graph.json   (AST symbols + import/call graph)
proofbraid graph    <repo>     -> print the graph
proofbraid vectors  <repo>     -> build hybrid-retrieval vector shards
proofbraid propose  <repo> "task" [--apply] [--provider meta|baseten|fireworks|all] [--sandbox local|modal]
    1. retrieve spans: hybrid lexical + local-vector fusion (RRF),
       lexical fallback when no vectors exist
    2. ask the chosen provider for new file contents
    3. write them to a disposable temp copy of the repo
    4. run the verification loop on the temp copy (locally, or in a Modal sandbox)
    5. on PASS: save the hypothesis, evict superseded ones, optionally --apply
    6. on FAIL: report failures, keep the real repo untouched
proofbraid verify   <repo>     -> run the verification loop on a repo directly
proofbraid daemon   <repo>     -> watch the repo, re-index on change
proofbraid hypotheses <repo>   -> list saved hypotheses
proofbraid providers          -> show each partner provider's role and config status
```

## How to run

```bash
cd project
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/proofbraid index examples/sample_repo
.venv/bin/proofbraid graph examples/sample_repo
.venv/bin/proofbraid verify examples/sample_repo
.venv/bin/proofbraid propose examples/sample_repo "fix the off-by-one in add()"
```

(No third-party dependencies: pure Python 3.10+ stdlib, so the install
is instant. Without the venv, `PYTHONPATH=src python3 -m proofbraid ...`
works too.)

## Meta Model API setup (still to do)

Proofbraid talks to the Meta Model API with the OpenAI-compatible Chat
Completions shape (see `src/proofbraid/meta_client.py`):

- Base URL: `https://api.meta.ai/v1`
- Auth: `Authorization: Bearer $MODEL_API_KEY`
- Default model: `muse-spark-1.3`

Set the key and go:

```bash
export MODEL_API_KEY="LLM|..."
python3 -m proofbraid propose <repo> "your task"
```

Without a key, `propose` fails cleanly with instructions instead of
pretending to work. Claim the hackathon's $150 in Meta Model API credits
(the claim form link came in the Cerebral Valley blast on Oct 26, 2026;
claim deadline Nov 4, 9:00 PM ET) and check the Billing page to confirm the
credits landed. Note: using the credits requires a card on file, with a
spend-limit notification available on the Billing page.

API details above come from third-party write-ups of Meta's docs, not from
a verified official doc read during scaffolding — confirm the endpoint and
model ID against dev.meta.ai before demo day.

## All partners, each with a distinct job

Every partner speaks the OpenAI-compatible Chat Completions shape, so one
generic client (`src/proofbraid/providers.py`) serves all of them. Endpoints
and key names are confirmed from the providers' own docs. Model IDs for
Baseten and Fireworks are NOT guessed: pick them from the provider's own
catalog (Baseten: `baseten model-api list` or GET /v1/models; Fireworks:
its model library, IDs carry the `accounts/fireworks/models/` prefix).

| Partner | Role | How to use |
|---|---|---|
| Meta Model API ($150) | Primary proposer: hosted Muse Spark generates diffs | `proofbraid propose <repo> "task"` |
| Baseten ($100 + 2M TPM) | Open-weights proposer: Muse Spark Open / Glimmer class models; also the path to the $10K Best Build on Meta Open Weights bonus | `proofbraid propose <repo> "task" --provider baseten` |
| Fireworks ($25) | Auxiliary calls: cheap, fast, small jobs (span ranking, hypothesis summaries) so Meta credits pay for diffs, not chores | automatic when configured; skipped silently when not |
| Modal ($100) | Sandboxed verification: untrusted model code runs its tests in an isolated sandbox instead of next to your real files | `proofbraid propose <repo> "task" --sandbox modal` |

```bash
export BASETEN_API_KEY='...' BASETEN_MODEL='...'     # open-weights proposer
export FIREWORKS_API_KEY='...' FIREWORKS_MODEL='...' # aux calls
export MODAL_SANDBOX=1 MODAL_TOKEN_ID='...' MODAL_TOKEN_SECRET='...' # sandbox verify
proofbraid providers   # shows each provider's role and config status
```

`--provider all` cascades meta → baseten → fireworks until one returns a
proposal, and reports every attempt. `--sandbox modal` falls back to local
verification (and says so) when Modal is not configured. The live Modal
sandbox path follows Modal's Sandbox API shape but has not been run
against real Modal credentials — verify it against Modal's Sandboxes docs
before demo day. Partner credits go to select participants, so apply
through their forms early.

## Current scope and honest limits

- The AST graph indexer covers **Python** today (stdlib `ast`). The seam
  for tree-sitter (other languages) is marked in `graph.py`.
- Retrieval is hybrid: lexical match fused with local vector similarity
  via reciprocal rank fusion (RRF, k=60), graph neighbours added only
  after direct results. Vectors are **hashed TF-IDF (stdlib, 256 dims),
  not neural embeddings** — the fusion method, stale-omission rule, and
  graph-after-direct ordering are the transfer from prior research on
  evidence-grounded retrieval, re-implemented fresh here; the embedding
  model itself is intentionally stdlib. Vectors for stale files are
  omitted from vector results, and similarity never bypasses the
  verification gate. Hybrid retrieval is experimental and unmeasured:
  lexical retrieval remains the proven fallback, and no retrieval-quality
  improvement is claimed until measured.
- Indexed files carry provenance: SHA-256 content hash plus git HEAD and
  per-file last commit when the repo is a git repo (absent otherwise).
- `propose` asks the model for **full new file contents** (JSON), not
  unified diffs. Unified-diff parsing is the next step.
- The verification loop runs `py_compile`, an optional linter if one is
  installed (ruff/flake8, skipped gracefully if absent), and pytest or
  unittest if tests exist. A repo with no tests still gets compile checks.
- `propose` defaults to dry-run: the real repo is never modified unless
  you pass `--apply` after a verified pass.
- The daemon polls file mtimes (simple, portable); inotify/FSEvents
  watching is a later optimisation.
```

## Roadmap to demo day (Nov 4, 9:00 PM ET / Nov 5, 7:30 AM IST)

1. Claim credits, first live `propose` call against the sample repo.
2. Unified-diff output mode (smaller, safer changes than full files).
3. tree-sitter indexing for JS/TS so the demo isn't Python-only.
4. A recorded demo: stale-interface bug fixed only because the graph
   caught it — the exact failure mode vector-only agents miss.
5. Partner forms: apply early for Baseten, Modal, and Fireworks credits
   (limited seats); then a live run of each partner path — Baseten
   open-weights propose, Fireworks aux summary, Modal sandbox verify.
