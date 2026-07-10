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

Recommended sequence: **MA-1 → MA-2 → MA-3 → MA-4 → MA-5 → MA-6**, with MA-7/MA-8
schedulable any time (no dependencies on the others).

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

---

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

---

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
