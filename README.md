# Proofbraid: a local coding agent that verifies before it applies

Proofbraid is a local developer tool. It stops AI coding agents from breaking code during long tasks that touch many files.

License: MIT, see LICENSE.

## The idea in one paragraph

Proofbraid is a dependency graph plus verify before apply plus hypothesis
eviction. It maps a repo into a real dependency graph from the Python
parser, retrieves only the code that is structurally related to a task,
and asks the Meta Model API (Muse Spark), or Baseten hosted Muse Spark
Open as the open weights second proposer, to propose a change. Before
anything touches the real files, it checks that change in a disposable
copy of the repo: compile, lint and tests. Verified fixes are recorded as
hypotheses, and older hypotheses on the same files are evicted so stale
claims never linger. A dry run is the default. Nothing is applied without
a verified pass and an explicit `--apply` flag.

## Commands

```
proofbraid index <repo>                 parse repo -> .proofbraid/graph.json
proofbraid graph <repo>                 print symbols and call edges
proofbraid vectors <repo>               build local hybrid retrieval shards
proofbraid propose <repo> "task" [--provider meta|baseten|custom] [--diff] [--retry N] [--apply]
proofbraid verify <repo>                run the verification loop directly
proofbraid hypotheses <repo>            list saved and evicted hypotheses
proofbraid providers                    show provider roles and config state
proofbraid daemon <repo> [--once]       reindex on file change
proofbraid mcp                          run the MCP server on stdio
```

What `propose` does:

1. It retrieves spans by fusing lexical matches with local vector
   similarity (RRF, k=60). Lexical retrieval is the proven fallback when
   no vectors exist.
2. It asks the chosen provider for a proposal. By default the model
   returns full new file contents as JSON. With `--diff` it returns one
   unified diff per file instead.
3. It writes the proposal into a disposable temp copy of the repo. In diff
   mode every diff is parsed strictly and applied in memory first. A
   malformed diff (bad header, bad hunk header, wrong line counts, context
   that does not match, unsafe path, or a file deletion) fails the whole
   proposal with a clear message, and nothing is partly applied.
4. It runs verification on the temp copy: `py_compile`, a linter when one
   is installed, and pytest or unittest when tests exist.
5. On a pass it saves the hypothesis, evicts superseded ones, and applies
   the change only with `--apply`. On a failure it reports the reasons and
   leaves the real repo untouched.

`--retry N` allows up to N total attempts (default 1, capped at 5). When
an attempt fails verification, the task, the failed proposal and the
failure output (cut to 4000 characters) go back to the same provider,
which is asked for a corrected proposal in the same format (full files,
or diffs with `--diff`). Every attempt is checked in its own fresh temp
copy against the repo's own tests, and `--apply` only fires after a
verified pass. The result JSON adds `attempts_used` and a per attempt
`attempts` list. Retries are bounded; there is no promise that the model
converges on a fix.

```bash
proofbraid propose examples/sample_repo "fix the off by one in add()" --retry 3
```

The code also accepts `--provider fireworks`, `--provider all` and
`--sandbox modal`. These exist in code but are not configured.

## How to run

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/proofbraid index examples/sample_repo
.venv/bin/proofbraid graph examples/sample_repo
.venv/bin/proofbraid verify examples/sample_repo
.venv/bin/proofbraid propose examples/sample_repo "fix the off by one in add()"
.venv/bin/proofbraid propose examples/sample_repo "fix the off by one in add()" --diff
python -m unittest discover -s tests
```

There are no third party dependencies. It is pure Python 3.10+ standard
library, so the install is instant. Without the venv,
`PYTHONPATH=src python3 -m proofbraid ...` works too. Runtime data lives
in `.proofbraid` inside the target repo. The test suite mocks every model
call and needs no keys.

## Partner API: Meta Model API (Muse Spark), the primary proposer

This is the default provider for every `propose` call (see
`src/proofbraid/meta_client.py` and `src/proofbraid/providers.py`).

- Base URL: `https://api.meta.ai/v1`
- Auth: `Authorization: Bearer $MODEL_API_KEY`
- Endpoint: POST `/v1/chat/completions`
- Default model: `muse-spark-1.3`. Set `PROOFBRAID_MODEL` to use another
  model, such as `muse-spark-1.3-contributor`.
- Proven live on Oct 27: `propose` on the sample repo returned
  `ok: true` and `verified: true` and recorded a hypothesis, as a dry run.

```bash
export MODEL_API_KEY="..."
proofbraid propose examples/sample_repo "fix the off by one in add()"
proofbraid propose <repo> "task" --apply   # only after a verified pass
```

Without a key, `propose` fails cleanly with instructions instead of
pretending to work.

## Partner API: Baseten hosted Muse Spark Open, the second proposer

Baseten serves Meta open weights through the same chat completions shape.
It is the open weights second proposer, used with `--provider baseten`.

- Base URL: `https://inference.baseten.co/v1`
- Auth: `Authorization: Bearer $BASETEN_API_KEY`
- Model: exactly `meta-models/muse-spark-open-1026`
- Proven live: `propose --provider baseten` on the sample repo returned
  `ok: true` and `verified: true` and recorded a hypothesis, as a dry run,
  in both full mode and `--diff` mode.

```bash
export BASETEN_API_KEY="..." BASETEN_MODEL="meta-models/muse-spark-open-1026"
proofbraid propose examples/sample_repo "fix the off by one in add()" --provider baseten
```

Without a key, `propose --provider baseten` fails cleanly with
instructions.

## Works with any AI

The contract is simple: any model behind an OpenAI compatible chat
completions endpoint (POST `/chat/completions` with a Bearer key). The
meta, baseten and fireworks presets are conveniences, not the limit.
`--provider custom` reads three environment variables, all required:

- `PROOFBRAID_BASE_URL`: the API base, ending before `/chat/completions`
- `PROOFBRAID_API_KEY`: the key sent as the Bearer token
- `PROOFBRAID_MODEL`: the model name the endpoint expects

If one is missing, `propose` stops before any network call and names the
missing variable. `proofbraid providers` shows whether custom is ready.
Example with a local Ollama server (Ollama ignores the key, but one must
be set):

```bash
export PROOFBRAID_BASE_URL="http://localhost:11434/v1"
export PROOFBRAID_API_KEY="ollama"
export PROOFBRAID_MODEL="qwen2.5-coder"
proofbraid propose examples/sample_repo "fix the off by one in add()" --provider custom
```

Scope: OpenAI compatible shape only. There is no native Anthropic or
Gemini API support; use them only through an OpenAI compatible gateway.
Note that `PROOFBRAID_MODEL` is also the model override for the meta
preset.

## Use from any AI agent (MCP)

MCP (Model Context Protocol) is the open standard that lets AI agents call
outside tools. `proofbraid mcp` runs a stdio MCP server (JSON-RPC 2.0, one
message per line) that exposes five tools:

- `index_repository(repo_path)`: index a repo, report files and symbols
- `get_graph(repo_path)`: symbols and call edges, first 200 symbols
- `verify_repo(repo_path)`: run verification, report pass or fail
- `propose_change(repo_path, task, provider, apply)`: the propose flow,
  dry run by default, apply only after a verified pass
- `list_hypotheses(repo_path)`: the recorded hypotheses

Point an agent at it by using `proofbraid mcp` as the stdio command, for
example:

```json
{
  "mcpServers": {
    "proofbraid": {
      "command": "proofbraid",
      "args": ["mcp"],
      "env": {"MODEL_API_KEY": "your key here"}
    }
  }
}
```

Scope: the minimal MCP surface only (`initialize`, `tools/list`,
`tools/call`). No resources, prompts or sampling.

## Modal and Fireworks

Code paths for a Modal sandbox (`src/proofbraid/sandbox.py`) and Fireworks
summaries (`src/proofbraid/aux_calls.py`, `src/proofbraid/providers.py`) exist in
the repo. They are not configured or tested live. Verification runs locally, and the Fireworks summary is skipped when no key is set.

## Scope and honest limits

- The indexer covers Python only, using the standard library `ast`
  module. There is no tree-sitter and no JS or TS support.
- `propose` returns full file contents by default. Diff mode (`--diff`) is
  new and optional, and it parses strictly: exact context only, with no
  fuzzy matching.
- Vectors are hashed TF-IDF (standard library, 256 dimensions), not
  neural embeddings.
- Hybrid retrieval is experimental and unmeasured. Lexical retrieval is
  the proven path.
- Verification runs locally. There is no live sandbox.
- Two proposers are supported: Meta Model API (primary) and Baseten hosted Muse Spark Open (second).
- `--provider custom` and `proofbraid mcp` are covered by mocked tests only and have not been run live against a model or an agent.
- Indexed files carry provenance: a SHA-256 content hash, plus git HEAD
  and the last commit per file when the repo is a git repo.
- The daemon polls file modification times, which is simple and portable.

