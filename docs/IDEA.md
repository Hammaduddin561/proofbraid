# Proofbraid: the idea in full, and the research behind it

*Prepared for the Meta Global AI Developer Hackathon (Indie Dev Track),
Oct 26, 2026. This document covers what the project is, why it should work,
what published research and practitioner evidence supports each design
choice, and what is still unproven. It is a working document, not submission
copy: the submission video must show only what was built during the event
(see §5).*

---

## 1. The idea in full

### One-line pitch

A lightweight, local developer tool that stops autonomous coding agents from
breaking down on long, multi-file tasks — by grounding every proposed change
in the repo's real structure and verifying it before it is allowed to land.

### The problem

Coding agents fail in long tasks in three specific, repeatable ways:

1. **Stale syntax.** The agent retrieves a function signature or interface
   from an old version of the code and writes new code against it. The result
   compiles in the agent's head and breaks in the repo.
2. **Broken interfaces across files.** A change to one file silently breaks
   callers in other files, because the agent never saw the dependency.
3. **Context bloat.** As a session goes on, the prompt fills with outdated
   hypotheses, superseded decisions, and irrelevant chunks. The agent's
   reasoning degrades even though its context window is nowhere near full.

Standard vector retrieval makes all three worse: it dumps the most
*similar-looking* chunks into the prompt, whether or not they are current,
structurally related, or even correct anymore.

### The solution

Proofbraid runs as a simple CLI plus a background daemon, in five steps:

1. **Map.** The daemon maintains a real-time AST dependency graph of the
   repo: every function, class, and method; what each one calls; what each
   file imports. The graph is re-indexed whenever files change, so it never
   describes yesterday's repo.
2. **Retrieve structurally, not by similarity.** For a task, Proofbraid pulls
   the symbols named in the task plus their callers and callees — the
   interfaces a change would actually touch — instead of the top-k most
   similar text chunks.
3. **Propose via Meta's Model API.** The grounded spans go to Muse Spark
   (with Muse Code as the coding agent surface) with one job: propose the
   change. The model is told to invent nothing it was not shown.
4. **Verify before anything lands.** The proposal is written to a
   disposable copy of the repo, never the real one, and checked against
   local linters, compilers, and the repo's own test suite. This is the
   pre-execution verification loop.
5. **Remember verified work, evict the rest.** A hypothesis that passes
   verification is saved with the content hashes it was verified against.
   When a newer verified hypothesis touches the same files, older ones are
   evicted — superseded context is removed before it can poison the next
   proposal. Failed hypotheses are kept, marked failed, so the agent does
   not retry the identical change blindly.

The real repo is modified only when verification passed *and* the user
passes `--apply`. By default every run is a dry run.

### Who it is for

Solo developers, running locally, with no massive cloud infrastructure.
Everything except the model call is local and free: the graph, the
verification, the memory. The only paid input is the Meta Model API call
itself, covered by the hackathon's $150 credits.

### A concrete scenario (the demo)

A repo has `add(a, b)` and `double(x)` which calls `add`. Somebody (or an
earlier agent session) leaves `add` off by one. A vector-only agent asked
to "fix the off-by-one" may retrieve stale usages, or confidently rewrite
`double` instead. Proofbraid retrieves `add` *and* its caller `double`,
proposes the fix, runs the repo's tests in a temp copy, watches them fail
before the fix and pass after it, saves the verified hypothesis, and only
then applies. The scaffold's sample repo demonstrates exactly this, and its
verification loop already catches the bug (tests report `6 != 5`).

### What it is not

- Not a basic RAG app: retrieval is structural (graph reachability), not
  similarity ranking, and retrieval is only step 2 of 5.
- Not a chatbot, dashboard, or Streamlit app: it is a CLI and daemon.
- Not a cloud service: it runs on the developer's machine.
- Not a repackaging of prior work: the event build is the Proofbraid scaffold
  written Oct 26–Nov 4. The earlier research engine below is background
  only and must not be presented as event work.

### Foundation (prior work, background only)

The idea grows out of Md's earlier solo research engine: an
evidence-grounded source memory and verification layer for coding
assistants, built around canonical source indexing (Git revision metadata
plus SHA-256 content-addressed provenance), an AST symbol extractor and
import call graph, lexical search fused with disk-sharded local vector
embeddings (reciprocal rank fusion) under bounded RAM, a shell-free
verification gate, semantic patch validation in disposable workspaces, and
a strict citation gate blocking unverified claims. That work is unpublished
and is described in the hackathon application only as "a local,
evidence-grounded source memory and verification engine." Its name stays
out of all public submission materials.

### Current state (Oct 26, 2026)

Scaffold built and smoke-tested: `proofbraid index / graph / verify /
propose / daemon / hypotheses`, pure Python stdlib, no dependencies.
5/5 smoke tests pass. Working: Python AST indexing, call-graph retrieval,
the verification loop (py_compile + optional linter + pytest/unittest),
hypothesis storage with eviction, the Meta API client (base
`https://api.meta.ai/v1`, model `muse-spark-1.3`, key from `MODEL_API_KEY`),
the polling daemon. Still open: claiming the $150 credits and the first
live model call, unified-diff output (today the model returns full file
contents), tree-sitter indexing for JS/TS, and pushing the project to a
public GitHub repo for submission.

### Submission fit

- Track: Indie Dev Track (solo). Theme: the "Push the Frontier" side —
  a new kind of coding agent, not a wrapper.
- Judging: stage 1 pass/fail (fits a theme, uses Meta AI products) needs
  the theme and products named explicitly; stage 2 rubric scoring (0–10)
  leans heavily on the demo video; stage 3 is a live final for the top 5
  per track on Nov 9.
- Bonus prizes: Best Agentic Use-Case ($5K) is the natural primary
  target; Most Viral ($20K) and Community Vote ($10K) are secondary
  targets served by the same demo video; Best Build on Meta Open Weights
  ($10K) only if an open-weights mode is added; Best Creative Build does
  not fit.
- Anti-projects (kickoff): none apply. Positioning rule: never call it
  "RAG" in public materials; lead with the AST graph and the
  verification loop.

---

## 2. The research behind it

Each design choice below is backed by published research or practitioner
evidence found Oct 26, 2026. Where a source is a blog or survey rather
than a peer-reviewed paper, that is stated. Nothing here is claimed as
proof that Proofbraid works — the evidence supports the *design*, and the
demo still has to prove the *implementation*.

### A. Context degrades as it grows — so "bigger window" is not the fix

- A 2025 Chroma study tested 18 frontier models and found **every one
  degrades in accuracy as input context length increases**, well before
  the window fills. Stanford's "lost in the middle" work shows accuracy
  dropping ~30% for information positioned in the middle of the window,
  with a U-shaped attention curve that persists as windows grow.
  ([HackerNoon summary](https://hackernoon.com/the-hardest-problem-in-ai-development-is-context);
  [Atlan on context distraction](https://atlan.com/know/context-distraction/))
- Cognition measured it in coding agents specifically: agents spend 60%
  or more of their first turn just searching, and **doubling task
  duration quadruples the failure rate**. The first hour of a session is
  sharp; by the third, the agent forgets earlier decisions and its
  suggestions drift from the codebase's patterns.
  ([HackerNoon](https://hackernoon.com/the-hardest-problem-in-ai-development-is-context))
- Practitioner taxonomies name four distinct failures: **context
  poisoning** (errors compound over time), **context distraction**
  (over-reliance on history), **context confusion** (irrelevant
  information degrades quality), and **context clash** (contradictions
  inside the window). Poor context files have been shown to *reduce*
  task success rates compared to using no context file at all.
  ([Augment Code guide](https://www.augmentcode.com/guides/context-engineering-enhancing-agentic-swarm-coding-through-intent-environment-and-system-memory))
- The "context wall" in real mature codebases: when the window floods,
  the agent hallucinates technically valid but functionally broken
  solutions — e.g. "modern" C that violates a project's compatibility
  and thread-safety rules.
  ([Gerhards, "The Context Wall"](https://rainer.gerhards.net/2025/12/the-context-wall-in-ai-code-generation.html))

**What this means for Proofbraid:** the core bet — *better context, not more
context* — is the consensus direction. Scoped, governed, current context
is the fix; a bigger window alone is not.

### B. Stale context is worse than no context

- Retrieval quality is a spectrum, not a binary: exact signatures are
  "signal" (+30% correctness in one practitioner's accounting), related
  patterns are "context", unrelated modules are "noise" (−10%), and
  contradictory or deprecated APIs are "poison" (−40%). Most RAG
  pipelines maximise retrieval *volume*, and a vector top-k returns the
  most *similar* results, not the most *useful* — including stale docs
  and conflicting information.
  ([dev.to, "The AI Agent Context Collapse"](https://dev.to/tamizuddin/the-ai-agent-context-collapse-why-more-documentation-makes-your-coding-agent-dumber-and-how-to-2594))
- Repository-level RAG systems rely on vector indexes built from
  snapshots. As code changes, the index goes stale: a modified function
  is no longer represented by its stored vector, so the retriever hands
  the model outdated context.
  ([RepoCoder survey discussion](https://www.researchgate.net/publication/376393643_RepoCoder_Repository-Level_Code_Completion_Through_Iterative_Retrieval_and_Generation))
- "Context distraction" is distinct from hallucination: the output is
  grounded in real data but incoherent, because the agent picked the
  wrong source or the wrong definition among conflicting ones.
  ([Atlan](https://atlan.com/know/context-distraction/))

**What this means for Proofbraid:** the daemon's job — re-indexing on every
change — is not a nice-to-have. A graph that is even slightly stale
recreates the exact failure the tool exists to fix. The staleness check
in the scaffold (hash comparison before every propose run) exists for
this reason.

### C. For code, structure beats similarity

- Repository-level code generation has a consistent retrieval hierarchy
  across surveys: lexical (grep/BM25) < semantic embeddings < **graph
  retrieval** (dependency graphs, call graphs, ASTs) < hybrid
  (semantic + structural). Graph retrieval captures cross-file
  relationships text similarity misses — a function importing a type
  from another module, a test exercising a specific path. Retrieved
  similar code can *degrade* generation accuracy by up to 15% by
  introducing noise, while graph retrieval gains most exactly where the
  needed dependencies share no vocabulary with the task description.
  ([AgentPatterns survey](https://agentpatterns.ai/context-engineering/repository-level-retrieval-code-generation/))
- **DADG** (Dynamic Abstract Syntax Tree Graphing, 2025) makes the
  dependency graph itself the retrieval index for change-impact tasks,
  ranking by graph reachability rather than similarity, and reports
  substantially fewer missed dependents than similarity baselines on
  five real packages. GraphRAG/GRAG show the same principle for general
  QA; DADG is the code-specific version, where the graph is the
  program's own call/reference structure recovered from the AST.
  ([DADG paper (PDF)](https://2505.in/papers/asg.pdf))
- **RepoCoder** (Zhang et al., 2023) iterates retrieval and generation;
  **RepoGraph** builds a line-level repository graph over dependency
  relations; **GraphCoder** adds control-flow and data-flow context
  graphs; **CodexGraph** (2025) puts the graph in Neo4j and lets the
  agent traverse it with generated queries ("find the class inheriting
  BaseHandler that implements process_request"). **PKG** (programming
  knowledge graphs) indexes AST nodes so retrieval returns whole
  functions, never fragments split across chunks.
  ([FeatLens related work](http://arxiv.cnoctave.top/pdf/2609.26480);
  [PKG synthesis](https://www.academia.edu/157009614/Structural_and_Semantic_Augmentation_in_Code_Generation_A_Critical_Synthesis_of_Programming_Knowledge_Graphs_and_Parallel_Retrieval_Architectures);
  [structure-aware indexing paper (PDF)](https://pdfs.assets.alphaxiv.org/2602.11671v2.pdf))

**What this means for Proofbraid:** the AST graph is the most
well-supported choice in the design. Proofbraid sits closest to DADG and
RepoGraph: graph reachability for change impact, whole-symbol spans
rather than chunks. Its honest difference from CodexGraph is
deliberate simplicity — no graph database, no generated query language,
just the repo's own structure in a JSON file, which keeps it local and
fast.

### D. Verification loops work — when the oracle is independent

- **Self-Debugging** (Chen et al., 2024) shows LLMs improving code by
  reflecting on execution feedback; later work separates
  post-execution debugging (which struggles, partly because
  self-generated tests carry label bias) from in-execution debugging
  (watching intermediate runtime states), which performs more
  consistently.
  ([arXiv:2501.12793](https://arxiv.org/pdf/2501.12793))
- **Reflexion** (Shinn et al., 2023) and **Self-Refine** (Madaan et
  al., 2023) use execution results or critiques to revise outputs;
  across these systems the strongest gains come from **independent
  test oracles**, not from the model's own judgement of its work.
  (Surveyed in [arXiv:2610.20935](https://arxiv.org/html/2610.20935v1))
- **ReVeal** (Jin et al., arXiv:2506.11442, ICLR 2026) structures
  long-horizon reasoning as iterative generation–verification turns
  with tool execution feedback, explicitly optimising verification
  itself, and sustains self-improvement for 20+ turns on
  LiveCodeBench. Its core idea — the verification–generation
  asymmetry, i.e. checking is cheaper and more reliable than
  generating — is Proofbraid's core idea too.
  ([arXiv:2506.11442](https://arxiv.org/abs/2506.11442v2))
- **Caution from the same literature:** a model can satisfy a verifier
  vacuously without an independent alignment check (AlphaVerus), and
  repair loops get less effective as rule complexity grows. The
  defence is exactly what Proofbraid does: the oracle must be independent
  of the generator. Proofbraid uses the repo's own test suite plus
  compilers and linters — artefacts the model did not write — rather
  than asking the model whether its own diff looks right.

**What this means for Proofbraid:** the verification loop is not a new
claim; it is the established mechanism, applied at the point where
other agents skip it — *before* the change lands, in a disposable
copy, with the repo's own tests as judge.

### E. Forgetting must be governed, not left to chance

- Practitioner memory designs converge on the same rule: compact when
  over budget, keep recent turns verbatim, never split a tool-call pair,
  and treat forgetting as a feature. One guide's formulation: without
  memory decay, agents become overwhelmed with irrelevant data; with
  exponential decay and pruning below a relevance threshold, they stay
  usable. "Forgetting is a feature, not a bug."
  ([dev.to, memory architecture guide](https://dev.to/oblivionlabz/building-ai-agent-memory-architecture-a-practical-guide-to-state-management-in-autonomous-systems-5c4j);
  [dev.to, eviction policies](https://dev.to/gabrielanhaia/agent-memory-in-typescript-short-term-long-term-and-what-to-throw-away-303b))
- The failure mode that matters is not forgetting but **remembering
  the wrong thing**: an ungoverned memory-aware agent becomes a
  reliability risk, and stale context from old sessions causes more
  problems than forgotten context does — which is why coding tools
  default to ephemeral session memory.
  ([Adaline, agent memory design](https://labs.adaline.ai/p/agent-memory-is-a-product-surface);
  [ninadpathak.com, coding-agent memory](https://ninadpathak.com/articles/coding-agent-memory/))
- Source-backed memory is an emerging answer: keep the exact captured
  text, record a SHA-256 hash and an origin label per entry, and hand
  the agent budgeted excerpts with references it can check, instead of
  a lossy summary it must trust. (One shipping attempt, JEV Context
  Fabric, is self-labelled alpha and mock-tested only — cited here as
  a related direction, not as validated evidence.)
  ([CompleteTech](https://complete.tech/blog/source-backed-memory-ai-coding-agents/))

**What this means for Proofbraid:** hypothesis eviction is the governed
version of forgetting. It does not summarise or compress — it deletes
superseded hypotheses outright when a verified replacement exists, and
it keeps content hashes so a hypothesis is only trusted against the
exact code it was verified on.

### Mapping: design choice → evidence → Proofbraid's difference

| Proofbraid choice | Evidence behind it | What Proofbraid does differently |
|---|---|---|
| AST dependency graph as the index | DADG, RepoGraph, GraphCoder, PKG (§C) | No graph DB, no query language; the graph is a local JSON file, rebuilt on change |
| Retrieve callers + callees, not top-k similar | Graph beats similarity; similar code can cost 15% (§C) | Retrieval is reachability from task-named symbols, plus their direct edges |
| Verify in a disposable copy before applying | Self-Debugging, Reflexion, ReVeal; independent oracles win (§D) | Verification gates the write itself; default is dry-run, `--apply` is opt-in |
| Evict superseded hypotheses | Forgetting as a feature; stale > forgotten (§E) | Eviction is deletion on verified replacement, with hash-checked provenance |
| Local-first, model call only for diffs | Context degradation grows with window size (§A) | Small grounded prompts instead of whole-repo dumps; token use stays low |
| Daemon re-indexing on change | Stale vector indexes return outdated context (§B) | Hash check before every propose run; stale graph triggers re-index |

---

## 3. Honest gaps — what the research warns about that Proofbraid has not solved

1. **Python only.** The graph indexer covers Python today. The
   tree-sitter seam is marked but unbuilt; a JS/TS demo repo would
   currently index nothing.
2. **Full-file output.** The model returns whole file contents, not
   diffs. That is safer to verify but wasteful on tokens and risky on
   large files; unified-diff output is the planned next step.
3. **Self-generated test bias.** If a repo's tests are weak, the loop
   verifies against a weak oracle and can pass bad changes. The
   literature's answer — independent oracles — is only as good as the
   repo's suite. The demo should use a repo with real tests, not toy
   ones, to stay honest.
4. **Verifier gaming.** A model that learns the checks can satisfy
   them vacuously (AlphaVerus caution). Mitigation in Proofbraid: the
   checks are the repo's own tests and compilers, not model-written
   assertions, and failures are reported, not hidden.
5. **Graph build cost.** Graph methods are the most expensive to build
   and maintain (survey hierarchy, §C). On a very large repo, full
   re-indexing on every change will lag; incremental indexing is
   future work, and the demo should stay on small-to-medium repos.
6. **Single-model dependence.** All diffs come from one model family
   via one API. A bad model day, an outage, or credit exhaustion stops
   proposing entirely. The rest of the tool still works (index, graph,
   verify, daemon) without the key.
7. **No measured results yet.** Everything in §2 supports the design,
   not this implementation. The credible claims at submission time
   are: the loop works end-to-end on the sample repo, verification
   catches real bugs, and token use stays small because prompts are
   grounded spans. Anything stronger needs measured runs (tasks fixed
   with graph vs without, tokens per task, verification catch rate).

---

## 4. Next steps to Nov 4, 9:00 PM ET

1. Claim the $150 Meta Model API credits (form from the Oct 26 blast;
   card on file required; spend-limit notification on).
2. First live `propose` run against the sample repo; record tokens used
   and whether the fix verifies.
3. Unified-diff output mode.
4. tree-sitter indexing for JS/TS (or scope the demo to Python honestly).
5. Push to a public GitHub repo; verify it opens logged-out.
6. Record the 3-minute demo video: the stale-interface failure, the
   graph catching it, the verification loop passing, the apply. Show
   only event-week work.
7. Submit on the CV platform by Nov 4 evening IST; post the demo on X
   for the Most Viral prize and the gallery for the Community Vote.

---

## 5. Sources

- Chroma (2025) long-context degradation study; Stanford lost-in-the-middle — via [HackerNoon](https://hackernoon.com/the-hardest-problem-in-ai-development-is-context) and [Atlan](https://atlan.com/know/context-distraction/)
- [Augment Code, context engineering guide](https://www.augmentcode.com/guides/context-engineering-enhancing-agentic-swarm-coding-through-intent-environment-and-system-memory)
- [Gerhards, "The Context Wall in AI Code Generation"](https://rainer.gerhards.net/2025/12/the-context-wall-in-ai-code-generation.html)
- [dev.to, "The AI Agent Context Collapse"](https://dev.to/tamizuddin/the-ai-agent-context-collapse-why-more-documentation-makes-your-coding-agent-dumber-and-how-to-2594)
- [DADG paper (PDF)](https://2505.in/papers/asg.pdf)
- [AgentPatterns, repository-level retrieval survey](https://agentpatterns.ai/context-engineering/repository-level-retrieval-code-generation/)
- [FeatLens related work (PDF)](http://arxiv.cnoctave.top/pdf/2609.26480)
- [PKG synthesis](https://www.academia.edu/157009614/Structural_and_Semantic_Augmentation_in_Code_Generation_A_Critical_Synthesis_of_Programming_Knowledge_Graphs_and_Parallel_Retrieval_Architectures)
- [Structure-aware indexing paper (PDF)](https://pdfs.assets.alphaxiv.org/2602.11671v2.pdf)
- [RepoCoder publication page](https://www.researchgate.net/publication/376393643_RepoCoder_Repository-Level_Code_Completion_Through_Iterative_Retrieval_and_Generation)
- [Self-Debugging revisit (arXiv:2501.12793)](https://arxiv.org/pdf/2501.12793)
- [ReVeal (arXiv:2506.11442)](https://arxiv.org/abs/2506.11442v2)
- [RAC paper, verification-and-repair survey](https://arxiv.org/html/2610.20935v1)
- [dev.to, agent memory architecture](https://dev.to/oblivionlabz/building-ai-agent-memory-architecture-a-practical-guide-to-state-management-in-autonomous-systems-5c4j)
- [dev.to, agent memory eviction policies](https://dev.to/gabrielanhaia/agent-memory-in-typescript-short-term-long-term-and-what-to-throw-away-303b)
- [Adaline, agent memory design](https://labs.adaline.ai/p/agent-memory-is-a-product-surface)
- [ninadpathak.com, coding-agent memory](https://ninadpathak.com/articles/coding-agent-memory/)
- [CompleteTech, source-backed memory](https://complete.tech/blog/source-backed-memory-ai-coding-agents/)
- Hackathon facts (track, credits, prizes, timeline, judging, submission rules) from the official Cerebral Valley event page, the Oct 26 host blasts, and the Oct 26 kickoff livestream slides.
