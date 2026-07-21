# Roadmap — Multi-Agent Handoff ("Agent Brain")

**Goal:** close the gap between today's system (excellent single-agent memory) and the
project's end state — a centralized memory core where **agent A writes, agent B picks up
the next job and retrieves the context it needs**, plugged into harnesses (Vercel AI SDK,
MCP, LangChain, CrewAI) as a robust shared brain for large-scale orchestration.

**Where this comes from:** a full assessment of DESIGN.md + code (2026-07-04) against
that goal. The storage/retrieval/security/durability foundations are done and strong;
what's missing is a thin coordination layer. Three load-bearing findings drive the order:

1. **No read-your-writes** — `store` returns `"queued"` and commits async
   (durable queue, §15) *plus* the ArangoSearch view commits on a 1 s interval
   (`schema/collections.py` → `commitIntervalMsec: 1000`). At a pipeline handoff, agent B
   can silently miss agent A's final writes.
2. **Cross-agent visibility is a convention, not a capability** — every retrieval arm
   hard-filters `doc.agent_id == @agent_id` (`retrieve/search.py`). Sharing works only by
   writing to a pseudo-agent namespace (`<crew_id>::query`, §14); reading "mine + shared"
   takes N separate calls with no cross-tier fusion.
3. **No handoff primitive** — "brief me for this task" doesn't exist; B must invent a
   retrieval query. Agent *outputs* aren't captured either (the Vercel middleware stores
   only the last user text), so A's conclusions never reach the brain unless they pass
   through tool calls.

Each item below is a self-contained work package (branch → PR → squash-merge, keyless CI
green) with scope, files, tests, and acceptance criteria. IDs are `MA-*` (multi-agent).
Sizes: S ≈ ≤1 day, M ≈ 2–3 days.

## Priority order

| # | ID | Item | Size | Depends on |
|---|----|------|------|-----------|
| 1 | MA-1 | Read-your-writes: `sync` store + `/v1/flush` barrier | S | — |
| 2 | MA-2 | Multi-`agent_id` retrieval with single-pass fusion | M | — |
| 3 | MA-3 | `POST /v1/prime` — task briefing endpoint | M | MA-2 |
| 4 | MA-4 | Capture assistant outputs in the Vercel middleware | S | — |
| 5 | MA-5 | Handoff eval (A writes → B retrieves, scored) | M | MA-1, MA-2 |
| 6 | MA-6 | Docs: multi-agent orchestration guide | S | MA-1..3 |
| 7 | MA-7 | Per-agent key binding + insight-tier write protection | S | — |
| 8 | MA-8 | Vector-index reliability + resume P1 benchmark | M | — |
| 9 | RQ-1 | Multi-hop query decomposition / iterative retrieval | L | shipped (helps on MuSiQue) |
| 10 | BX-1 | Benchmark expansion: MuSiQue multi-evidence dataset + metric | M | — |
| 11 | RQ-2 | Close the retrieval-content gap (diagnostic → cross-encoder reranker) | L | shipped (+0.135 recall) |
| 12 | BX-2 | Pooled-corpus MuSiQue (open-retrieval variant, tests first-stage recall) | S | BX-1 |
| 13 | BX-3 | Lightweight pooled diagnostic (extract-skip + graph-off; routes around O(n²) wall) | S | shipped (33% first-stage gap) |
| 14 | SC-1 | Single-large-tenant scalability: ANN entity resolution + bounded graph fan-out | L | — |
| 15 | RT-1 | Expose `candidate_pool` as a config + API knob (open-corpus tuning) | S | — |

Recommended sequence: **MA-1 → MA-2 → MA-3 → MA-4 → MA-5 → MA-6**, with MA-7/MA-8
schedulable any time (no dependencies on the others). MA-1…MA-8 are **shipped**. **RQ-1**
(multi-hop retrieval) is also shipped as opt-in `mode="multihop"`; its value is
**benchmark-dependent** — neutral/harmful on LoCoMo (single-turn gold) but **+0.165 all-hops
recall on MuSiQue** (genuinely multi-evidence; see the RQ-1 outcome note below and DESIGN §23).
**BX-1 is shipped** (multi-evidence metric + MuSiQue converter), which enabled that re-trial.
**RQ-2 is shipped:** the diagnostic found MuSiQue misses are 100% ranking-bound, and the
cross-encoder reranker (`rerank=true`) it pointed to lifted all-hops recall **0.430 → 0.565**
on MuSiQue. Rerank and multihop are different, composable levers and they **stack
super-additively — `MODE=multihop RERANK=--rerank` reaches 0.810 all-hops / 0.905 recall-frac**
(the recommended max-recall config; DESIGN §23). No further scheduled item.

**Companion:** [GUILD.md](GUILD.md) redesigns the `examples/dungeon` demo around this
work — expendable heroes, a torch-as-context-window budget, and a Handoff Briefing
screen that renders `/v1/prime` live. Its E-1/E-3/E-4 packages run on today's core;
E-2 is the visible payoff of MA-1 + MA-2 + MA-3.

---

## MA-1 — Read-your-writes: `sync` store + `/v1/flush` barrier

**Problem.** `POST /v1/store` enqueues a durable write intent and returns `"queued"`;
a worker commits it later, and BM25 visibility lags a further ≤1 s behind the commit
(ArangoSearch `commitIntervalMsec: 1000`). An orchestrator that runs
`A.store(...) → B.retrieve(...)` gets intermittent missing context — a correctness hole
at exactly the handoff moment.

**Design.**
- `StoreRequest` gains `sync: bool = False` (same on `/v1/step`). When `sync=true`, the
  handler processes the intent **inline** (same code path the worker runs — reuse, don't
  fork logic), then forces search-view visibility before responding. Response `status`
  becomes `"committed"` (vs `"queued"`).
- View visibility: after commit, either issue the AQL no-op that forces a view sync
  (query the view with `OPTIONS { waitForSync: true }`) or poll for the written doc key in
  the view with a short deadline (~2 s). Prefer the `waitForSync` view option — measure
  both against the §23 latency budget and document the choice.
- `POST /v1/flush` (barrier): body `{ ctx }`. Blocks until the queue has **no pending or
  claimed intents for that tenant** and the view is synced, with a `timeout_ms` (default
  5000) → `{"status": "flushed"}` or `{"status": "timeout", "pending": n}` (HTTP 200 both
  ways — callers branch on status; a timeout is not a server error).
  - Queue support: extend the `WriteQueue` Protocol (`ingest/queue.py`) with
    `pending_count(tenant_id) -> int`. Trivial for `InProcessQueue`; a filtered COUNT for
    `ArangoQueue`.
- Adapters: expose `sync` on the TS client + `flush()` helpers in `lib`-level clients
  (Vercel package option `syncWrites?: boolean`; MCP gains a `flush` tool; LangChain /
  CrewAI pass-through kwargs).

**Files.** `core/src/arango_memory/api/app.py` (models + handlers),
`core/src/arango_memory/ingest/queue.py` (+ both backends),
`core/src/arango_memory/ingest/` (worker's commit fn factored for inline reuse),
`packages/vercel/src/index.ts`, `core/src/arango_memory/mcp/tools.py`, docs (`api.md`,
DESIGN §15/§19).

**Tests.** `sync=true` → store then immediate BM25 retrieve finds the text (no sleep);
`flush` returns only after a queued intent lands (enqueue N, flush, assert visible);
flush timeout path (stall the worker with a fake queue); idempotency preserved under
sync (replay same idempotency_key). All keyless (FakeEmbedder), testcontainers.

**Acceptance.** A `store(sync=true) → retrieve` round-trip in one test with **zero
sleeps** passes deterministically; `flush` documented in `api.md`; §15 updated to state
the consistency model explicitly (async by default, opt-in barrier).

**Out of scope.** Cross-instance flush fan-out (single-instance semantics documented;
the Arango-backed queue makes multi-instance flush *mostly* correct already — note the
caveat, don't solve it).

---

## MA-2 — Multi-`agent_id` retrieval with single-pass fusion

**Problem.** All three retrieval arms filter to exactly one `agent_id`
(`retrieve/search.py:46,60,81`). "My private memory + crew shared + crew insights"
requires three HTTP calls and client-side merging that bypasses the core's RRF fusion,
decay, belief, and salience boosts.

**Design.**
- `AccessContext` gains `read_agent_ids: list[str] | None = None` (default `None` →
  current single-`agent_id` behavior; nothing changes for existing callers). Writes are
  untouched — `agent_id` remains the sole write identity.
- AQL: `doc.agent_id == @agent_id` → `doc.agent_id IN @agent_ids` in all three arms
  (vector, BM25, graph). Bind the list once; keep tenant filter untouched (isolation is
  tenant-level and already tested — this must not loosen it).
- Fusion is automatically cross-tier once candidates flow from one query — no fusion code
  change expected. Add `agent_id` (provenance) to `MemoryHit` so consumers can weight or
  display "who wrote this".
- Authz: in enforced mode, every id in `read_agent_ids` must still belong to the key's
  tenant (already guaranteed by the tenant filter — assert it in a test, not new code).
  Per-agent restrictions are MA-7, not here.
- CrewAI storage (`crewai/storage.py`): teach `search()` to pass
  `read_agent_ids=[own, crew::query, crew::insight]` in **one** call instead of per-tier
  lookups; keep the tier objects for writes.

**Files.** `core/src/arango_memory/api/app.py` (`AccessContext`, `MemoryHit`),
`core/src/arango_memory/retrieve/search.py` (all arms + scope dict),
`core/src/arango_memory/crewai/storage.py`, `packages/vercel/src/index.ts`
(`readAgentIds?: string[]` option), MCP `search` tool arg, `api.md`, DESIGN §14
(upgrade "schema-ready" → "first-class").

**Tests.** Two agents write distinct facts; retrieve with `read_agent_ids=[a, b]` returns
both, ranked by one fusion pass; provenance field correct; default (`None`) returns only
own memories (regression); cross-**tenant** id in the list yields nothing (isolation
holds); CrewAI one-call path returns tiered results.

**Acceptance.** One `retrieve` call spans N namespaces with fused ranking + provenance;
existing single-agent tests unmodified and green.

**Out of scope.** A `visibility` field on memories (private/crew/tenant enum) — the
namespacing convention + explicit id lists cover current consumers; revisit if a real
consumer needs doc-level ACLs.

**Future enhancement — cross-agent corroboration display.** When several agents wrote
the *same* fact, MMR de-dupes them by embedding similarity and the survivor carries only
one agent's provenance — so a briefing can't show "both A and B knew this." Correct for
ranking, but a future pass could surface corroborating authors (a `also_known_by: [...]`
on a hit) when multiple agents independently recorded a fact. Deferred (agreed): not
needed for the handoff use case; MMR collapsing duplicates is the right default for now.

---

## MA-3 — `POST /v1/prime`: task briefing endpoint

**Problem.** The handoff verb is missing. Agent B (or the orchestrator) should say
"here's the task, brief me" and get one budgeted context package — not hand-craft a
query and hope. (This absorbs the "prime endpoint" idea from the beads comparison in
`future-investigations`.)

**Design.**
- `POST /v1/prime` body: `{ task: str, ctx, opts? }` where `opts` adds
  `include: {episodic: bool, semantic: bool, procedural: bool}` (all true) on top of the
  usual `mode`/`k`/`max_memory_tokens`. `ctx` supports `read_agent_ids` (MA-2).
- Composition (no new retrieval machinery):
  1. **Episodic/semantic hits** — existing `retrieve(task, ...)` pipeline as-is.
  2. **Entities** — top-N semantic entities among the hits' mentions (existing
     projections; include belief/salience fields already surfaced by CC/SAL work).
  3. **Procedural** — match `steps` against the task text (BM25 over tool
    names/args/outcomes; reuse the steps read path from `/v1/steps`), so B sees *how*
    similar jobs were done, including failures.
  4. **Assembly** — one markdown-ish briefing string (mirrors `retrieve`'s `context`
     contract) with sections `## Relevant history`, `## Key entities`,
     `## Prior tool runs`, packed under `max_memory_tokens` with a fixed section budget
     split (e.g. 50/25/25, truncate lowest-scored first). Also return the structured
     parts (`hits`, `entities`, `steps`) so richer consumers can self-assemble.
- Response: `{ context: str, hits: [...], entities: [...], steps: [...],
  tokens_injected: int }`.
- Adapters: MCP tool `prime` (the flagship consumer — any MCP harness gets handoff
  for free); TS client `prime()`; LangGraph `prime` node variant of `recall`.

**Files.** `core/src/arango_memory/api/app.py`, new
`core/src/arango_memory/retrieve/prime.py` (assembly logic, unit-testable pure
functions), `mcp/tools.py` + `mcp/server.py`, `packages/vercel/src/index.ts` (exported
helper, not middleware), `api.md`, DESIGN §19 (add to the contract table).

**Tests.** Briefing contains all three sections when data exists; token budget respected
(inject oversized corpus, assert ≤ budget); section omitted cleanly when `include` flag
off or no data; procedural matching finds a step by tool name; works with
`read_agent_ids` spanning a writer agent and a reader agent (the actual handoff shape);
MCP tool contract test.

**Acceptance.** The scenario in this doc's header is demo-able with two curl calls:
`store` as agent A (sync), `prime` as agent B with `read_agent_ids=[A, shared]` — B's
briefing contains A's facts and tool history.

**Out of scope.** LLM-generated briefing summaries (assembly is deterministic
extraction; a Haiku "summarize the briefing" pass is a future `mode: full` enrichment).
Job/queue semantics — claiming, status, scheduling stay in the orchestrator
(LangGraph/Temporal); document the integration pattern in MA-6 instead. Revisit the
"atomic entity claim" idea from `future-investigations` only if a real consumer needs
work-item locking inside the memory layer.

**Shipped as MA-3a** (core: `retrieve/prime.py` + `POST /v1/prime` + tests + docs). Two
scope adjustments made against what's actually built, both agreed: (1) **entities** are
derived from the retrieved memories' `mentions` (task-relevant + read-scoped) rather than
a tenant-wide `list_entities`; (2) **prior tool runs** rank by `use_count` across the read
agents — *task-semantic step matching* (BM25 over tool args) is a **future enhancement**,
since `steps` aren't in a search view. Adapters (MCP `prime` tool, TS `prime()`, LangGraph
prime node) are **MA-3b**, folded into the adapter PR.

---

## MA-4 — Capture assistant outputs in the Vercel middleware

**Problem.** `wrapGenerate`/`wrapStream` store only `lastUserText(params.prompt)`
(`packages/vercel/src/index.ts:197,205`). Agent A's *output* — its analysis, plan,
answer — is what agent B usually needs, and today it never reaches the brain unless it
went through a tool call.

**Design.**
- `wrapGenerate`: after `doGenerate()` resolves, extract assistant text from
  `result.content` (text parts) and store it as a second turn:
  `content = "[assistant] " + text` with the same ctx (or a `role` metadata field if the
  store contract grows one — prefer the prefix; zero core change).
- `wrapStream`: tap the stream (`text-delta` parts) and store the accumulated text
  **onFinish** — must not delay time-to-first-token; store remains fire-and-forget
  (`void`).
- Config: `captureResponses?: boolean` on `ArangoMemoryOptions`, default **true**
  (symmetric with `captureToolTraces`). Truncate at a sane cap (e.g. 4 kB) before
  storing — long generations dilute retrieval; the episode stays the audit anchor.
- Mirror in the LangChain `remember` node and note in the CrewAI guide (CrewAI's own
  hooks already save task output — verify, don't duplicate).

**Files.** `packages/vercel/src/index.ts` (+ its vitest suite),
`core/src/arango_memory/langchain/` (nodes), adapter docs.

**Tests (vitest).** generate path stores user + assistant texts (two `/v1/store` calls,
assert bodies); stream path stores accumulated text only after stream end; flag off →
single store; failure of the assistant store never rejects the turn.

**Acceptance.** After one wrapped turn, retrieving for content that appeared *only in
the model's answer* returns a hit.

**Out of scope.** Storing full multi-step reasoning traces (tool traces already cover
the act/observe loop); summarizing responses before store.

**Shipped in the adapter surface PR** together with the client halves of MA-1/2/3:
Vercel middleware `captureResponses` (MA-4, default on), `syncWrites` (MA-1b),
`readAgentIds` (MA-2b), and standalone `prime()`/`flush()` helpers (MA-3b/MA-1b); MCP
`prime` + `flush` tools and `read_agent_ids` on `search`. The dungeon app adopts these
in E-1/E-2, not here.

---

## MA-5 — Handoff eval: A writes → B retrieves, scored

**Problem.** The eval suite (LoCoMo) scores single-agent conversational recall;
concurrency tests prove *isolation*. Nothing measures *sharing* — the headline
multi-agent claim is unproven and unguarded against regression.

**Design.**
- New `core/src/arango_memory/eval/handoff.py` + dataset
  `core/tests/data/handoff_smoke.json`: scripted **work sessions** (not chats) — agent A
  ingests facts + records tool steps under a shared namespace; agent B (different
  `agent_id`) issues task-shaped queries via `prime`/`retrieve` with `read_agent_ids`.
  Score: **context recall** (fraction of gold facts present in B's briefing),
  **procedural recall** (gold tool runs surfaced), tokens injected.
- Scenarios (≥4): clean handoff (A→B, shared tier); three-stage pipeline (A→B→C, C needs
  facts from both); distractor noise (irrelevant memories from other agents in-tenant
  must not crowd out gold facts at the token budget); sync-boundary (B reads immediately
  after A's final `sync` store — exercises MA-1, zero sleeps).
- Runner mirrors `eval/benchmark.py` conventions: CLI module, per-scenario progress to
  stderr, targets table, **nonzero exit below targets**; `make handoff-eval`; wire the
  smoke slice into CI next to the existing eval smoke (keyless: FakeEmbedder).

**Files.** `core/src/arango_memory/eval/handoff.py`, `core/tests/data/handoff_smoke.json`,
`core/tests/test_handoff_eval.py`, `core/Makefile`, `.github/workflows` (existing core
job picks it up via pytest), DESIGN §22 (add the harness), §23 (targets: start with
context recall ≥ 0.8 / procedural recall ≥ 0.6 on the smoke slice; tighten with data).

**Tests.** The eval *is* the test; plus unit tests for the scorer (gold-fact matching is
token-overlap like `eval/locomo.py` — reuse, don't reimplement).

**Acceptance.** `make handoff-eval` passes keyless in CI; deliberately breaking
cross-agent reads (e.g. reverting MA-2's AQL) fails the gate.

**Shipped** (`eval/handoff.py`, `tests/data/handoff_smoke.json`,
`tests/test_handoff_eval.py`, `make handoff-eval`). Targets: context recall ≥ 0.8,
procedural recall ≥ 0.6. Reader reads via `prime` (3 scenarios) + one `retrieve`-only
(sync-boundary); the smoke slice gates via pytest in the Core CI job. The planned
separate `sim/` tree was folded into `eval/`.

## MA-6 — Docs: multi-agent orchestration guide

**Problem.** After MA-1..4 the capability exists but is spread across four adapters, two
new endpoints, and a namespacing convention. Nobody can discover the intended pattern.

**Scope.** New `docs/orchestration.md` (linked from README + docs index):
- The pattern: tenant = workspace, agent_id = worker identity, shared namespaces
  (`<crew>::query` / `<crew>::insight`), `read_agent_ids` for reads, `sync`/`flush` at
  stage boundaries, `prime` at stage start.
- A worked pipeline example end-to-end (curl or Python): planner → researcher → writer,
  each stage priming from the previous stage's writes.
- Per-harness recipes: Vercel middleware options; MCP tool sequence for a
  Claude-Desktop-style agent; LangGraph two-node graph with `prime`; CrewAI
  `crew_memory`.
- Integration guidance: what stays in the orchestrator (job queue, retries, scheduling,
  triggering the next agent) vs what the brain provides (context, provenance, history) —
  including the explicit decision that job semantics are out of scope and why.
- Consistency model section (async default, barrier opt-in) — the sharp edge, stated
  plainly.
- Update DESIGN §14 to reflect first-class status and link here.

**Acceptance.** A newcomer can build the three-stage pipeline from the doc alone against
a local compose stack; lychee link check green.

**Shipped** (`docs/orchestration.md`, linked from the docs index + DESIGN §14 + the
adapters index). LangGraph recipe uses the in-process `prime()` call directly (no
`prime` graph node was added). Also fixed the adapters index (9 → 11 MCP tools).

## MA-7 — Per-agent key binding + insight-tier write protection

**Problem.** An API key maps to tenant + scope only (`ApiKeyEntry`); any keyholder may
assert any `agent_id` — one compromised worker key can impersonate every agent and write
directly to `::insight` (which is supposed to be Dream-State-only, currently enforced
purely by adapter convention).

**Design.**
- `ApiKeyEntry` gains `agent_ids: list[str] | None = None` (None → any, current
  behavior). In `_authorize` (`api/app.py`), when the principal restricts agents:
  body `ctx.agent_id` (writes) must be in the list → else **403** (mirror the existing
  tenant-mismatch style); `read_agent_ids` entries outside the list are **silently
  dropped** (reads degrade, never break — §15 spirit). Supports glob-lite suffix
  (`"research::*"`) for crew keys.
- Insight protection: server-side rule — writes to an agent_id matching `*::insight`
  require the principal (or open mode) to carry a new scope value `consolidate`
  (`scope: Literal["read","write","consolidate"]`, ordered write < consolidate). Dream
  State runs in-process (no HTTP hop) so internal consolidation is unaffected.
- OIDC parity: optional `oidc_agent_claim` mapping to the same restriction.

**Files.** `core/src/arango_memory/config.py`, `core/src/arango_memory/security/auth.py`,
`core/src/arango_memory/api/app.py` (`_authorize`), tests
(`test_authz*.py`), `docs/ops.md` + `api.md` key examples, DESIGN §17.

**Tests.** Bound key writing as an allowed agent → 200; as another agent → 403; read
list silently filtered; unbound key unchanged (regression); `::insight` write with
`write` scope → 403, with `consolidate` → 200; open (keyless) mode fully unchanged.

**Acceptance.** A worker key can be scoped to its own agent + the crew's shared tier;
the demo deployment gets a scoped key without any dungeon change (its key lists
`dungeon-player`… i.e. `agent_ids:["dm"]` + tenant as today).

**Shipped.** `ApiKeyEntry.agent_ids` + ordered `scope` (`read < write < consolidate`,
`scope_allows`); `Principal.agent_ids` + `agent_allowed` (glob-lite suffix); `_authorize`
enforces agent binding on writes (403), filters cross-agent reads silently (§15), and
gates `*::insight` writes behind `consolidate`; OIDC parity via `oidc_agent_claim` +
`consolidate` in the scope claim. Wired on `store`/`step` (writes) and `retrieve`/`prime`
(read filter). Fully backward compatible — `agent_ids=None` and open mode unchanged.
Tests: `test_authz_agents.py` + JWT-parity cases (all in-process, CI-gated). Docs:
`ops.md`/`api.md` key examples + `OIDC_AGENT_CLAIM`.

---

## MA-8 — Vector-index reliability + resume P1 benchmark

**Problem.** The brain's retrieval quality is its IQ, and the vector arm is currently
the weak leg: the P1 LoCoMo run is paused on a still-undiagnosed `retrieve degraded`
(see memory: `benchmark-run-state`), and the live demo's vector arm is degraded because
ArangoGraph can't pass `--vector-index`. Real-embedding quality is demonstrated nowhere.

**Scope (bundles the three improvement PRs already identified while troubleshooting).**
1. **Compose hardening:** raise `vm.max_map_count` automatically (privileged init
   service in `docker-compose.yml`) — removes the arangod crash-under-index-build class.
2. **Legible errors:** call `configure_logging()` in `eval.benchmark` / `eval.halu` /
   `ops` CLIs so failures print the actual `AQLQueryExecuteError` reason instead of bare
   `retrieve degraded` (this opacity cost a full day).
3. **n_lists sanity:** lower default `VECTOR_N_LISTS` (e.g. 64) and/or defer index
   creation until corpus ≥ `n_lists × factor`; document the `n_lists ≪ corpus` rule +
   a troubleshooting entry; surface index state in `/health`.
4. **Resume the benchmark** per the saved checklist (clean single arango, mmap fix,
   `down -v`, n_lists=16, capture the real AQL error if it persists) → complete the
   §23 lite run, record results in DESIGN.
5. **Managed-deploy note:** document the ArangoGraph limitation and the supported
   posture (BM25+graph lite mode) in `ops.md` / `orchestration.md`.

**Files.** `docker-compose.yml`, `core/src/arango_memory/eval/*.py`,
`core/src/arango_memory/ops.py`, `config.py`, `schema/` (index gating), `docs/ops.md`,
DESIGN §7/§23.

**Acceptance.** Fresh-clone `docker compose up` + `make benchmark DATASET=converted.json
MODE=lite` completes without manual sysctl or password surgery; any failure prints a
real error string; §23 report recorded.

**Shipped (infra + reliability; the run itself is user-executed).** (1) compose
`sysctl-init` raises `vm.max_map_count` before arangod; (2) the eval/ops CLIs call
`configure_logging()` and the degrade path now logs `str(exc)` (the real AQL reason), not
just the class name; (3) `vector_n_lists` 256→**64** + new `vector_train_factor` (**40**)
defers index creation to `n_lists × factor` docs via `vector_training_threshold`, so IVF
centroids train on enough points; (4) `/health` reports `vector: trained|deferred|unknown`;
(5) `ops vector-diag` surfaces the raw failure; docs cover the `n_lists ≪ corpus` rule, the
rebuild-needed-on-`n_lists`-change gotcha, the resume checklist, and the ArangoGraph
BM25+graph posture. **Deferred (needs the user's machine):** the actual LoCoMo §23 run +
recording results — everything above de-risks it. All CI-gated (338 core tests pass,
including new threshold/health/diag coverage).

---

## RQ-1 — Multi-hop retrieval (query decomposition) — SHIPPED, benchmark-dependent

> **Outcome (2026-07-19):** Built and merged as opt-in `mode="multihop"` (#134–#137). The
> result **depends on whether the benchmark's evidence is actually multi-turn:**
> - **LoCoMo — negative:** lite 0.317 vs multihop 0.132. LoCoMo multi-hop `gold_fact`s are
>   **single evidence turns**, so the questions are multi-hop in *reasoning*, not *retrieval*;
>   the full query matches the one gold turn best and decomposition only dilutes it.
> - **MuSiQue — positive** (200-Q smoke via BX-1, genuinely multi-evidence): **all-hops
>   Recall@k 0.430 → 0.595 (+0.165, +38%)**, recall-frac 0.682 → 0.777, F1 0.307 → 0.376.
>   The strictest metric (whole-chain retrieval) moves most — the mechanism works as designed.
>
> **Verdict:** a correct, opt-in mode that **helps when recall needs ≥2 evidence turns** and
> is neutral-to-harmful on single-turn-gold sets — use it accordingly. The original-query
> superset fix (#137) makes it safe (multihop = single-shot's hits + the extra hops). Full
> analysis in DESIGN §23. The original hypothesis below is retained for the record.

*Scoped, built, shipped. The design below is the original plan; see the outcome note above.*

**Problem.** The P1 LoCoMo run plateaued at **Recall@k ≈ 0.42** (from 0.215 — see
DESIGN §23). Single-shot retrieval maxed out: BM25 carries it, and every ranking bug
(#125–#131) is fixed. The residual gap to the 0.6 target is **category-bound**:

| Category | Recall | Ceiling reason |
|---|---|---|
| single-hop | ~0.36 | reachable — BM25 finds the one evidence turn |
| temporal | ~0.33 | reachable |
| **multi-hop** | **~0.19** | **the anchor** — see below |

A multi-hop question — *"Where does the person Alice met at the reunion work?"* — needs
**two evidence turns that don't co-locate near one query embedding**: turn A ("Alice met
Bob at the reunion") and turn B ("Bob works at Acme"). The query matches A but not B; B is
only findable *after* "Bob" is known. No single top-k pass over any arm weighting can
gather that chain. The existing **graph arm** expands one hop, but it seeds from the
*query's* hits and expands by static `relates_to` edges — it surfaces "things related to
Alice," not "Bob's employer." RQ-1 makes the second hop **query-directed** via the
generator. This is the one lever left, and it's a retrieval **mode**, not a knob.

**Approach (locked): decomposition first.** One generator call splits the question into the
minimal set of independent lookups; retrieve each with the existing fused `retrieve()`;
union + re-fuse; answer from the union. Deterministic, bounded latency, no agentic loop.
The iterative **read→retrieve→read** variant (handles dependent chains where hop 2 needs
hop 1's *result*) is strictly more powerful but is a serial loop — **deferred**; add it only
if multi-hop recall stalls below ~0.35 after decomposition. LoCoMo multi-hop is dominantly
*parallel* evidence (both facts stated independently), so decomposition should capture most
of the lift; measure it before paying for the loop's complexity.

**Algorithm.**
```
retrieve(mode="multihop", Q):
  subqs = decompose(Q, generator)              # → [Q] if model returns 0/1 lookups
  if len(subqs) <= 1: return _retrieve_impl(Q) # transparent single-shot fallback
  lists = [ _retrieve_impl(sq, k=sub_k) for sq in subqs[:decompose_max_subqueries] ]
  fused = rrf_fuse(lists)                       # each sub-query is an arm at weight 1.0
  selected = mmr(fused, k)                      # unchanged
  context = assemble_tiered(selected, budget)  # same token budget
  answer from context                          # locomo #131 step already does this
```
- **Transparent fallback** (0/1 sub-questions ⇒ identical to today's `retrieve()`) is the
  safety property: a mis-fire never scores *below* single-shot, it just adds nothing.
- **Fuse via `_rrf_fuse`**, each sub-query's list as an input arm at weight 1.0 (all are
  true relevance rankers, unlike the bm25/vector/graph asymmetry). A doc found by *multiple*
  sub-questions accumulates RRF mass — exactly the multi-hop signal to reward. Reuses
  existing fusion math; no new code.
- **Budget unchanged:** more sub-queries widen the candidate *pool*, not the injected
  context — downstream token cost to the agent stays flat.

**Surface (locked): new `mode="multihop"`.** Extend the `mode` Literal to
`lite|full|multihop`, orthogonal to full-mode enrichment (HyDE/gate). Default stays `lite`;
`multihop` is opt-in like HyDE, with documented N× latency (outside the §23 lite target).

**Files.**
- `retrieve/decompose.py` (new) — `decompose(query, *, generator, cache) -> list[str]` +
  `_DECOMPOSE_SYSTEM`, mirroring the `enrich.py` pattern (system prompt + one
  `generator.complete()` + `QueryCache` memoization).
- `retrieve/search.py` — `multihop` branch in `_retrieve_impl`; factor the per-query core so
  it's callable per sub-query.
- `config.py` — `decompose_max_subqueries: int = 4` (ge=1, le=8);
  `decompose_max_hops: int = 0` (reserved for the iterative variant; 0 = off).
- `api/app.py` + models — extend the `mode` Literal.
- `eval/locomo.py` — already threads `mode`; run with `mode="multihop"`.
- `docs/DESIGN.md §9`, `docs/ops.md` — document the mode, knobs, latency.
- tests — `test_decompose.py` (prompt/parse/fallback via `FakeGenerator`), a multihop-path
  test in eval/search.

**Acceptance.** Measured on the **real** LoCoMo dataset (not the smoke slice):
multi-hop recall rises materially above 0.19 (target this pass: **≥ 0.30**); single-hop and
temporal **do not regress** (guaranteed by the transparent fallback); overall Recall@k moves
toward 0.6; latency documented; default-mode latency unchanged.

**Risks.** (1) Bad decomposition retrieves noise — bounded by the sub-query cap + fallback;
worst case ≈ single-shot. (2) Latency multiplier — bounded by the cap, off the hot path.
(3) Eval cost — a real-generator multihop run over 1531 Qs makes N× retrievals + a decompose
call each; smoke on the multi-hop subset first and estimate cost before the full run.

---

## BX-1 — Benchmark expansion: MuSiQue multi-evidence dataset + metric

*Scoped, not started. Prerequisite for a meaningful RQ-2 and a fair RQ-1 re-trial.*

**Why.** RQ-1 could not be tested on LoCoMo because its multi-hop `gold_fact` is a **single
evidence turn** — recall (`_recall_hit`: one substring in any hit) rewards finding that one
turn, so decomposition has no headroom and its dilution only hurts (DESIGN §23). To measure
*multi-hop retrieval* — or any retrieval-quality lever — honestly, the benchmark must pair
**multi-evidence questions** with a **multi-evidence recall metric**. A better dataset alone
does nothing without the metric change; the metric is the load-bearing part.

**Dataset (chosen): [MuSiQue](https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00475/110996) (MuSiQue-Ans).**
24.8k 2–4-hop questions, **non-shortcuttable** by construction (Disconnection Filtering forces
each hop to depend on the prior — a single-paragraph baseline drops from ~65 F1 on HotpotQA to
~32 here), with **sub-question decomposition + paragraph-level support annotations**. It is the
only option that ships decomposition labels, so it directly re-trials the RQ-1 hypothesis on
data that is provably multi-hop. Alternative if a RAG-native, news-domain set is preferred:
[MultiHop-RAG](https://arxiv.org/abs/2401.15391) (2,556 queries, evidence across 2–4 docs).
Domain mismatch (documents vs conversation turns) is acceptable — our ingest is generic (a
supporting paragraph is just a memory) and we are testing retrieval *mechanics*.

**Design.**
- **Converter** (`eval/musique_convert.py`) → our dataset schema, but `QA.gold_fact: str`
  becomes `gold_facts: list[str]` (the support set). Ingest each supporting paragraph as a
  memory; keep the single-`gold_fact` path working (wrap as a 1-element list) so LoCoMo runs
  are unaffected.
- **Metric** (`eval/locomo.py`) — `_recall_hit` gains a multi-evidence mode: **fraction of the
  support set retrieved** (report both mean fraction and all-hops-present@k). This is what makes
  the benchmark able to reward gathering a chain.
- **Runner** — dataset-agnostic; a `--dataset-kind musique` flag or auto-detect on the schema.

**Acceptance.** MuSiQue converts + runs end-to-end; the multi-evidence recall metric is unit-
tested; LoCoMo numbers are unchanged (backward-compatible single-fact path). No retrieval-code
change — this is eval infra.

**Decisions to make at scope time.** all-hops-present vs fractional recall as the headline
(recommend reporting both, fractional as primary); MuSiQue-Ans subset size for a smoke;
paragraph-as-memory granularity (whole paragraph vs sentence).

---

## RQ-2 — Close the retrieval-content gap (diagnostic → reranker / expansion)

*Scoped, not started. Depends on BX-1 for a dataset with headroom; diagnostic-first.*

**Problem.** After the ranking fixes, the residual gap from Recall ≈ 0.42 → the 0.6 target is
a **retrieval-content** gap: question↔evidence lexical overlap ≈ 0.27 (DESIGN §23), so BM25
misses evidence that shares little vocabulary with the question, and the vector arm ranks by
query-proximity rather than relevance. But "content gap" has **two opposite failure modes**,
and building the wrong fix wastes effort:
- **Ranking failure** — the gold *is* in the candidate pool but ranked outside top-k → a
  **reranker** fixes it.
- **First-stage recall failure** — the gold is *absent from the pool* → a reranker cannot
  help; you need better first-stage recall (query expansion, stronger embeddings, richer
  `prospective_queries`).

**Design — diagnostic-first** (the measure-before-building discipline that saved three tuning
runs on RQ-1):
- **RQ-2a — diagnostic (cheap, no new model). Scoped in detail; decisions locked.** For every
  support item that *misses* (not in top-k), classify it against the **full fused candidate
  pool** (BM25 ∪ vector ∪ graph, post-RRF, pre-MMR/truncation), gathered in **lite single-shot**
  at the default **`candidate_pool=100`**:

  ```
  in top-k hits?            → HIT           (not a miss)
  else in the fused pool?   → RANKING miss  (a reranker can recover it)
  else (absent from pool)   → RECALL  miss  (first-stage retrieval must be fixed)
  ```

  The aggregate split (overall + per-category) is the whole deliverable — it picks RQ-2b's
  lever. **Files:** `retrieve/search.py` — add `diagnose_pool(...)` returning the full ranked
  fused pool via the existing `_gather_fused` (read-only, off the hot path, mirrors
  `diagnose_vector`); `eval/pool_diag.py` (new) — per-question classify `support()` items via
  the existing normalized-substring match, aggregate, CLI
  `python -m arango_memory.eval.pool_diag DATASET.json [--k 10] [--pool 100]`; `test_pool_diag.py`;
  ops.md run steps. No change to `retrieve()`'s hot path. **Acceptance:** produces the
  ranking-vs-recall split reproducibly on the MuSiQue 200-Q set (real embeddings).

  > **RQ-2a result (2026-07-20, MuSiQue 200-Q, #144):** of 400 support items, **303 hit /
  > 97 miss**, and the misses split **100% ranking (in-pool) / 0% recall (absent)**. Every
  > missed gold paragraph is already in the fused pool — the first stage finds all the
  > evidence; the failure is **purely ranking**. → **RQ-2b = cross-encoder reranker** (query
  > expansion has no gap to close). Headroom: 97/400 items (24%) recoverable by reordering.
  > *Caveat:* each MuSiQue question is its own ~20-paragraph tenant, so `pool=100` ⊇ the whole
  > corpus and a recall-miss is structurally near-impossible here — this proves the failure is
  > ranking **in the given-context setting** and validates the reranker, but does not test
  > first-stage recall on a large open corpus (that would need a pooled-corpus variant).

- **RQ-2b — cross-encoder reranker (lever, decided by 2a). Scoped; decisions locked.** Insert
  a reranker between fusion and MMR: fused pool (candidate_pool) → cross-encoder scores each
  `(query, text)` jointly → reorder → existing MMR + tiered assembly on the reranked order. A
  cross-encoder scores relevance directly (not lexical/proximity), so it recovers the 97
  in-pool-but-unranked golds. One hook in `_retrieve_impl` after `_gather_fused`; MMR/assembly
  unchanged (they consume `fused_score`).

  **Locked decisions:**
  - **Provider — local cross-encoder** (sentence-transformers, e.g. `bge-reranker-base` /
    ms-marco MiniLM). Pluggable `Reranker` protocol + a **`FakeReranker`** (deterministic,
    keyless) mirroring `Embedder`/`Generator`, so CI stays offline.
  - **Surface — composable `rerank=true` flag** (config/opts, orthogonal to `mode`), so it
    stacks on lite **or** multihop; not its own mode.
  - **Scoring — replace:** `fused_score := rerank score` for the reranked top-N; MMR then
    orders by pure cross-encoder relevance (cleanest measure of the lever's lift).
  - **`rerank_top_n`** bounds cost (default e.g. 50). Off the lite hot path; **degrades** to
    the fused order if the model is unavailable/errors (§15) — never breaks the turn.

  **Files:** `retrieve/rerank.py` (protocol + Fake + local provider), a `_rerank` hook in
  `search._retrieve_impl`, `config.py` (`rerank_enabled`, `rerank_top_n`, `reranker_provider`,
  model id), `eval` wiring (a `--rerank` benchmark flag), docs, tests.

**Acceptance.** Measured on **MuSiQue** (BX-1): the reranker lifts `recall-frac` / all-hops
`Recall@k` materially over the fused baseline (0.682 / 0.430) toward the diagnostic ceiling
(per-item recall 0.76 → ~1.0), without regressing lite-mode latency; degradation path tested.

---

## BX-2 — pooled-corpus MuSiQue (open-retrieval variant)

*Scoped, not started. Decisions locked. Eval infra only — no retrieval-code change.*

**Why.** Every MuSiQue result so far (lite/rerank/multihop/stacked) is in the **given-context**
regime: the converter makes each question its own **~20-paragraph tenant**, so `pool@100` ⊇
the whole corpus and the gold is *always* in the pool. That's why RQ-2a measured **0%
first-stage-recall misses** — an *artifact*, not a finding. We have never tested whether
BM25+vector+graph can **find** the gold among real distractors. BX-2 pools all questions'
paragraphs into **one shared corpus (one tenant)** so retrieval is genuinely open (thousands
of candidates), answering two open questions: (1) is first-stage recall a real gap on open
corpora (do out-of-pool misses finally appear in `pool_diag`)? (2) do the rerank/multihop/
stacked gains survive the harder regime?

**Design (locked).**
- `musique_convert.py` gains a **`--pooled`** flag (`convert(..., pooled=True)`): emit **one
  Sample** whose `sessions` hold every paragraph across the selected questions, and whose `qa`
  is **all** those questions (each keeping its `gold_facts`). Single `sample_id` = one tenant
  = one open corpus.
- **`--limit N` selects the questions**, and only *those* questions' paragraphs are pooled;
  the **same N questions are scored** — self-contained, so every gold is guaranteed present
  in the corpus and a miss is a *true* first-stage-recall failure (not a missing document).
- **Dedup** paragraphs by `(title, paragraph_text)` — MuSiQue reuses Wikipedia paras across
  questions; without dedup the corpus balloons and double-counts.
- Output is the standard schema, so `benchmark`, `pool_diag`, `--rerank`, `MODE=multihop` all
  run on the pooled file unchanged.

**Files.** `eval/musique_convert.py` (the `--pooled` branch + dedup), `test_musique_convert.py`
(pooled mapping: shared corpus, dedup, all questions retained), ops.md (build + run steps;
"open-retrieval stress test").

**Acceptance.** A pooled `musique-pooled.json` converts (one sample, N questions, deduped
corpus); `pool_diag` on it yields a **non-trivial out-of-pool (recall) miss fraction** — i.e.
it can surface the first-stage-recall failures the per-question setup structurally couldn't.
Then lite/rerank/multihop can be re-measured in the open-corpus regime.

**Decisions to make at build time.** dedup key exactness (title+text vs normalized); whether
to cap corpus size independently of `--limit` if ingestion is too slow; whether `pool_diag`'s
`--pool` should widen for the larger corpus.

> **BX-2 run outcome (scalability wall).** The first real pooled run (3,075 paragraphs, one
> tenant) took **~12 h to ingest** and then **timed out on retrieval** (`ReadTimeout` at 60 s),
> so it produced no usable recall split. Two bottlenecks the per-question 20-doc tenants had
> masked: (1) ingestion is **~O(n²)** — every `store()` resolves extracted entities against all
> existing entities in the tenant, which grows with corpus size; (2) the **graph arm fans out**
> on a dense single-tenant `relates_to` graph, blowing the retrieval timeout. → motivates BX-3
> (a lightweight probe that routes around both) and a separate scalability investigation
> (unscheduled).

---

## BX-3 — lightweight pooled diagnostic (routes around the BX-2 scalability wall)

*Scoped, not started. Decisions locked. First-stage-recall probe only — no scalability fix.*

**Why.** BX-2's pooled run hit a wall (O(n²) ingestion + graph-arm retrieval timeout, above).
But first-stage recall — "do BM25 + vector surface the gold among distractors" — needs
*neither* entity extraction nor the graph arm. BX-3 measures exactly that by skipping both, so
the open-corpus recall question is answerable in **minutes, not hours**.

**Design (locked).**
- **`store(..., extract=True)` param** (core): gate the existing `write_entities` block
  (`if is_new and not is_working and extract`). Default `True` = unchanged; `False` skips
  entity/edge extraction → no O(n²) resolution, no graph built. Minimal, generally reusable.
- **`pool_diag --lightweight` flag**: ingest with `extract=False` and probe with
  `graph_hops=0` (both the top-k `retrieve` and `diagnose_pool`). Measures first-stage recall
  (BM25 + vector) cleanly.
- **Record the scalability finding** in DESIGN §23 (the BX-2 outcome above); the O(n²)
  ingestion / graph fan-out **fix is a separate, unscheduled investigation** — BX-3
  deliberately routes around it, does not fix it.

**Files.** `ingest/store.py` (`extract` param), `eval/pool_diag.py` (`--lightweight` →
extract-skip ingest + `graph_hops=0`), tests (extract=False writes no entities; lightweight
path), ops.md, DESIGN §23.

**Acceptance.** A `--lightweight` pooled run over ~200 questions completes in minutes with no
retrieve timeouts and yields a real ranking-vs-recall split for first-stage recall on the open
corpus — the number BX-2 set out to get.

---

## SC-1 — single-large-tenant scalability (ANN entity resolution + bounded graph fan-out)

*Scoped, diagnostic-first. The highest-leverage open item: a long-lived agent's normal
end-state is a large single tenant, and BX-2/BX-3 proved the system stalls there
(~12 h to ingest 3k memories; retrieval times out).*

**Mechanism — confirmed by code, not just hypothesis.**
- **Ingestion is O(N²).** `entities._FETCH_EXISTING` (in `ingest/entities.py`) fetches **every
  entity in the tenant, *with its embedding*,** on every `store()`, then matches in Python by
  cosine. There is **no vector index on `entities`**. So per-write cost is O(N) transfer +
  O(N) compares and grows as the tenant fills → O(N²) overall.
- **Retrieval fans out.** The graph arm's `relates_to` traversal over a dense single-tenant
  entity graph blows the 60 s query timeout (the fan-out tamed at 200 docs, unbounded at 3k).

**Design — diagnostic-first, then two fixes.**
- **SC-1a — profiling harness (diagnostic, no core change).** Ingest into one tenant at growing
  sizes (e.g. 500 → 3,000) and record per-`store()` and per-`retrieve()` p50/p99 vs size, to
  (1) confirm the O(N²) ingestion curve + the retrieval blow-up and (2) set the baseline the
  fixes are verified against.
- **SC-1b — ANN entity resolution (the ingestion fix).** Add a **vector index to `entities`**
  (reuse the MA-8 IVF / `APPROX_NEAR_COSINE` machinery the `memories` arm already has) and
  replace the `_FETCH_EXISTING` full-scan with a **top-k nearest-entity** query per extracted
  entity — per-write O(N) → ~O(k). **Behaviour-preserving:** same `entity_merge_threshold`,
  only faster candidate generation; fall back to the scan while the index is cold/untrained
  (as the memory arm does, §7). Re-run SC-1a to prove the curve flattened.
- **SC-1c — bounded graph fan-out (retrieval fix, conditional on SC-1a).** Cap the traversal
  (neighbours per entity / seed count / degree) so retrieval stays bounded on a dense tenant.
  The graph arm is already down-weighted (`RRF_GRAPH_WEIGHT=0.1`), so a cap costs little
  quality. Sized after SC-1a quantifies it.

**Files.** `schema/collections.py` (entities vector index), `ingest/entities.py` (ANN
resolution + cold-start fallback), `retrieve/search.py` (`_GRAPH_QUERY` fan-out caps),
`config.py` (entity-index + fan-out knobs), a profiling script under `eval/` or `ops`, tests,
docs.

**Acceptance.** On the profiling harness, per-`store()` latency is ~flat (not linear) in tenant
size after SC-1b, and a pooled-corpus ingest + retrieve that previously stalled completes in a
reasonable time with no timeouts. Retrieval quality on the given-context benchmarks is
unchanged (behaviour-preserving resolution + down-weighted graph).

**Decisions locked (recommended).** candidate generation = **ANN vector index on `entities`**
(reuses MA-8; not blocking/LSH); **cold-start fallback to scan** while the entity index is
untrained; graph fix = **cap fan-out** (don't remove the arm). Open at build time: entity
`n_lists`/`train_factor`; the top-k and fan-out caps (tune on the harness).

---

## RT-1 — expose `candidate_pool` as a config + API knob (open-corpus tuning)

*Scoped. The cheap, direct payoff of the BX-3 finding.*

**Why.** BX-3 showed ~15% of open-corpus recall misses are *tail-reachable* by widening the
candidate pool past 100 (then rerank promotes them). But `candidate_pool` is a hardcoded
`retrieve()` arg default — no `CANDIDATE_POOL` env var, not in the API — so a large-tenant
deployment can't act on the finding (`rerank_enabled` already is tunable; the pool isn't).

**Design.** `config.py`: `candidate_pool: int = Field(default=100, ge=1)`. `retrieve()`:
`candidate_pool: int | None = None` → `settings.candidate_pool` (the `None → settings` pattern
`graph_hops`/`n_lists` use). API `RetrieveOptions`: add `candidate_pool`, threaded to
`retrieve`. Docs: an "open-corpus / large-tenant tuning" note (raise `CANDIDATE_POOL` + enable
rerank; trade is per-query latency). Tests: default, settings-default honoured, API thread.
**Keep the global default at 100** — widening it for everyone taxes the common given-context
path; the finding is open-corpus-specific, so make it *tunable*, not *bigger-by-default*.

**Acceptance.** `CANDIDATE_POOL` settable via env + `/v1/retrieve` opts; default runs are
byte-identical to today; docs explain the tuning. Size S.

---

## Explicitly out of scope (decided, with reasons)

- **Job/task queue semantics** (claim, status, schedule, retry): the orchestrator's job
  (LangGraph, Temporal, queues). The brain provides context + provenance + history;
  MA-6 documents the seam. Revisit "atomic entity claim" only on concrete demand.
- **Event push / webhooks / changes feed** ("wake agent B when A writes X"): valuable
  but a new infrastructure class (delivery guarantees, subscriber state). Poll via
  `retrieve`/`prime` for now; design doc first if demand materialises.
- **Doc-level ACLs / visibility enums**: namespacing + `read_agent_ids` + MA-7 key
  binding cover the known consumers with far less complexity.
