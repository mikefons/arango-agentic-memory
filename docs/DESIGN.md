# ArangoDB Agentic Memory System — Design Specification

> **Status:** ✅ **v1 build sequence complete (Steps 0–7).** v2: all §21 adapters shipped (MCP, LangChain/LangGraph, CrewAI) + full §19 entity API + **Step 3e heavy extraction tier done**. Authoritative reference.
> **Last updated:** 2026-06-09 (rev 34 — Memory Dungeon Graph Explorer tab)
>
> **Rev 2 decisions:** Python-first core with a thin TypeScript client · v1 scope is Vercel-only · build a walking skeleton first, then a test/eval harness, then thicken each layer.
>
> **Rev 3 updates (from Step 0):** ArangoDB image is **Enterprise 3.12.9.1** (Community tops out at 3.12.4.3; runs in evaluation mode without a license) · vector-index flag renamed `--experimental-vector-index` → `--vector-index` · **two configurable connection targets** (local Docker + ArangoGraph) via `ARANGO_TARGET` + a `check` probe · Vercel middleware uses `LanguageModelV2Middleware` (`ai@5` + `@ai-sdk/provider@2`), not V4 · dev tooling/infra captured in §25 · build sequence Step 0 marked done.
>
> **Rev 4 updates (from Step 1):** FastAPI core is now an **app factory** (`create_app(client=None)` + a `get_client` dependency) so tests inject a client; the module-level `app = create_app()` still serves `make dev`/prod · tests use **testcontainers** (Enterprise 3.12.9.1, eval mode) with a **fresh per-test database** for isolation · HTTP contract tests pin the TS↔Python seam via `TestClient` · the **LoCoMo runner is lite/BM25-only** for now (Recall@k + token-F1), with lite-vs-full comparison deferred to Step 2 · CI (`.github/workflows/ci.yml`) runs `make ci` · build sequence Step 1 marked done.
>
> **Rev 5 updates (requirement):** captured the **agentic simulation harness** as real-data validation of the end-to-end product (an agentic Vercel app using ArangoDB for memory *and* actions). Two parts — a deterministic CI harness (`sim/`) and a reference Next.js app (`examples/vercel-agent/`) — added to the monorepo (§3) and specified in §22; lands as **Step 3.5** (§24), after full mode + procedural ingestion + durable writes exist. Clarifies that the Step 1 smoke eval validates plumbing, not real-data quality.
>
> **Rev 6 updates (from Step 2a):** **Step 2 split into 2a (core retrieval, done) and 2b (full-mode enrichment, next).** 2a delivered a **pluggable sync `Embedder`** (deterministic `FakeEmbedder` for keyless tests/sim · `OpenAIEmbedder`) — a sync deviation from §8's async sketch · write-time embeddings on `memories` · a **lazy Faiss IVF index** that trains only once the corpus ≥ `n_lists` (ArangoDB ERR 1555 otherwise), self-healing on the read path, with BM25 cold-start fallback (§7) · retrieval is now **BM25 + vector → RRF → MMR → tiered token budget**; MMR works in the BM25-only path since embeddings live on the docs · `mode` is threaded but inert until 2b · **graph expansion stays deferred to Step 3** (needs entities/edges).
>
> **Rev 7 updates (from Step 2b):** full-mode enrichment is live (§9 stages 1–2), so the **lite/full switch is now meaningful**. Added a **pluggable sync `Generator`** (deterministic `FakeGenerator` with a scriptable handler for keyless CI · `AnthropicGenerator` on `claude-haiku-4-5` with system-block prompt caching) · **adaptive gate** (`should_skip_retrieval` → memory-less turn when the model is confident) · **HyDE** (embeds a hypothetical answer; falls back to the raw query when generation is empty) · a per-query **`QueryCache`** for both (§16). With the default fake generator, full mode degrades to the lite vector path — a *meaningful* lite-vs-full quality comparison needs a real/scripted model and is the Step 3.5 sim harness's job.
>
> **Rev 8 updates (from Step 3a):** **Step 3 split into 3a (extraction → graph, done), 3b (graph expansion, next), 3c (durable write path), 3d (procedural + prospective indexing).** 3a added a **pluggable sync `Extractor`** (deterministic `FakeExtractor` for keyless CI · `SpacyExtractor` behind the extra; **GLiNER/torch + Haiku fallback deferred** to 3d) · edge collections `mentions`/`relates_to`/`produced_by` + the `memory_graph` named graph + a unique entity natural-key index · entity **UPSERT** (exact dedup + `mention_count`) with embeddings and idempotent edges (`relates_to` from co-occurrence) · **write-time conflict detection** (§8 Stage 3): cosine vs the tenant's entities → ≥0.9 merge / ≥0.6 flag `needs_review` for Dream State / else new · extraction runs only on the first store of a turn so replays don't double-count. The graph is now populated, unblocking graph expansion (§9 stage 4, Step 3b).
>
> **Rev 9 updates (from Step 3b):** **graph expansion is live** (§9 stage 4) — a traversal from the entities of the top lexical/vector hits (`mentions` → `relates_to` 0..`graph_hops` → `mentions`) surfaces connected memories, ranked by minimum hop distance and **fused into retrieval as a third RRF signal** (`source: graph`) alongside BM25 + vector. Tenant-scoped; a no-op when a turn produced no entities. The §9 retrieval pipeline is now complete (stages 1–6) bar deferred tuning. Next: Step 3c (durable write path).
>
> **Rev 10 updates (from Step 3c):** the **durable write path** (§15) is live — `/v1/store` now **enqueues** an idempotency-keyed `WriteIntent` and returns immediately (`{status:"queued", episode_id, memory_ids}`, deterministic from the key); a background `WriteWorker` (daemon thread, own DB connection) drains the queue via `store()` with exponential backoff and **dead-letters to `failed_writes`** on exhaustion, with `replay_failed()` to recover. The queue is an in-process `WriteQueue` Protocol (Redis/SQS slot in later). `store()` stays the commit function (worker + tests). **Naming deviation:** the dead-letter collection is `failed_writes`, not §15's `_failed_writes` — ArangoDB reserves `_*` for system collections. Next: Step 3d (procedural + prospective indexing).
>
> **Rev 11 updates (from Step 3d):** **procedural memory + prospective indexing are live**, completing Step 3's ingestion thickening (3a–3d). Procedural (§5, §11): a `steps` collection + `TOUCHED` (step→memory) / `TRANSITION` (step→step) edges; `record_step` UPSERTs by `(tenant, agent, tool_name, outcome)` so a recurring pattern **increments `use_count`** (the reuse signal); `get_steps` lookup; a `StepIntent` rides the same durable queue (worker dispatches by type). API: `POST /v1/step`, `GET /v1/steps`. Prospective indexing (§8 Stage 4, full mode): `store()` generates hypothetical future questions (2b generator) into `memories.prospective_queries`, which the search view indexes so a memory is findable by a question it answers. **Procedural memory now exists → the Step 3.5 sim harness is unblocked.** The only remaining ingestion piece is **Step 3e** (GLiNER/GLiREL + Haiku extraction fallback — the torch tier). Next: Step 3.5 (agentic simulation harness).
>
> **Rev 12 updates (from Step 3.5a):** the **deterministic simulation harness** is live (`arango_memory/sim/`) — `run_scenario` plays a multi-session agent loop *with interleaved tool calls* against the core's HTTP surface (the endpoints the adapter calls) over a decoupled `HttpClient` Protocol, with stubbed models so it's reproducible and keyless. The CI gate (`test_sim.py`) asserts the four §22 categories: cross-session **recall** (lite + full), **procedural** memory + `use_count` reuse + `TOUCHED`/`TRANSITION` edges, graceful **write-failure degradation** (turn stays `queued`, retrieval degrades to memory-less), and tenant **isolation**. **Placement deviation:** the harness lives at `arango_memory/sim/` (mirroring `eval/`), not a standalone root `sim/` (§3), so it reuses the testcontainers fixtures and runs in the existing `make ci`. A *true* lite-vs-full quality delta still needs a real model → Step 3.5b. Next: Step 3.5b (reference Vercel app + adapter tool-trace capture).
>
> **Rev 13 updates (from Step 3.5b):** the **Vercel adapter now captures procedural memory** and there's a **runnable reference agent**. The adapter pairs `tool-call` + `tool-result` parts from the prompt history (deduped by `toolCallId`, chained via `prev_step_key`) → `POST /v1/step`; outcome comes from the result's output type (`error-*` → failure). Best-effort and non-blocking, with an inherent one-turn lag (a LanguageModel middleware only sees a tool's outcome on the *next* turn). **vitest** unit tests (mocked fetch + fake model) cover retrieve/inject, memory-less degradation, store, and tool capture (success/failure, dedup, chaining); a new **`adapter` CI job** (typecheck + build + test) runs alongside `core`. **`examples/vercel-agent/`** is a minimal `generateText` loop wrapping `arangoMemory` with a tool — the manual/nightly end-to-end check (adapter → core → ArangoDB), typechecked against `ai@5` + `@ai-sdk/anthropic@2`. The full Next.js chat UI is deferred to **Step 3.5c**. Next: Step 3e or Step 4.
>
> **Rev 14 updates (from Step 4a):** **episodic decay is live** (§11), split as the rev-4 decision: **lazy** at query time + a **scheduled sweep**. `lifecycle/decay.py` provides `effective_strength` (`strength·exp(-λ·Δdays)`), `decay_sweep` (soft-deprecates memories below `decay_floor` via `invalid_at`; never deletes), and `reset_access` (spaced repetition). Retrieval multiplies each candidate's fused score by `effective_strength` (the §9 stage-5 recency/access boost) and refreshes `accessed_at`/`access_count` on surfaced memories. AQL gotchas recorded: `lambda` is reserved, and unary minus on a bind param mis-parses. Working-memory TTL/SCM deferred; sweep scheduling is an ops concern (Step 7). Next: Step 4b (bi-temporal + Supersedes + conflict resolution).
>
> **Rev 15 updates (from Step 4b):** **bi-temporal foundations + the Supersedes mechanism** are in (§5, §12). Entities and all edges now carry `valid_time` (= ingestion_time), `valid_time_explicit` (false), `invalid_at` (null); edges also carry `weight` (1.0). New `Supersedes` edge collection + `lifecycle/conflict.py:supersede(new_key, old_key)` — writes `Supersedes` (new→old) and soft-deprecates `old` (`invalid_at`), idempotent. Graph traversal now filters `entity.invalid_at`/`related.invalid_at`, so a superseded entity stops bridging the graph. Decided **machinery-only**: `needs_review` stays written-but-unconsumed; the *decision* to supersede (confirm an ambiguous conflict) is Dream State's job in 4c. Explicit temporal parsing deferred to 3e; EWA `weight` deferred. Next: Step 4c (consolidation + Dream State worker + circuit breaker).
>
> **Rev 16 updates (from Step 4c — Step 4 complete):** **consolidation / Dream State** is in (§13). `lifecycle/dream.py:run_dream_state(db, tenant_id, generator)` is a threshold-driven pass over flagged (`needs_review`) + well-attested (`mention_count ≥ threshold`) entities, two-phase (decide → circuit-breaker → apply). It **finally consumes the `needs_review` flags**: Haiku confirms a flagged entity vs its `conflict_with` target → `CONTRADICTS` ⇒ `supersede()` + clear; `DISTINCT` ⇒ clear. Well-attested entities get a distilled one-sentence `summary` + `consolidated_at` (new entity fields). A **circuit breaker** halts the whole run (applies nothing) if planned supersessions exceed `dream_breaker_threshold` — a poisoning safeguard. GAM session-topic trigger deferred (separable subsystem); callable pass, scheduling → ops/Step 7. **Step 4 (lifecycle) is now complete (4a/4b/4c).** Next: Step 5 (security).
>
> **Rev 17 updates (from Step 5a):** **write-path security** is in (§17). New `security/` package: `redact.py` (regex redactor for email/SSN/card/API-keys/bearer → typed placeholders, conservative so prose/numbers pass through; plus a full-mode generator pass for contextual PII) and `worm.py` (`worm_guard` / `WORM_COLLECTIONS` / `WormViolation` — the client-layer enforcement primitive for the insert-only `episodes`). `store()` redacts `content` **first**, so the idempotency key, episode, memory, embedding, and entity extraction all operate on redacted text — the original is never persisted. Note: full mode now makes **two** generator calls (redaction + prospective), so stubbed generators must be system-aware. `redact_pii` config (default true). **Embedding encryption-at-rest is a DB-deployment concern** (ArangoDB Enterprise storage encryption), not app code, since field-level encryption would break vector search. Next: Step 5b (right-to-be-forgotten + ABAC).
>
> **Rev 18 updates (from Step 5b — Step 5 complete):** **right-to-be-forgotten + ABAC** are in (§17). `security/forget.py`: `forget()` soft-deletes (sets `invalid_at` on the subject's memories + entities → out of every retrieval surface at once); `purge()` is the ops-triggered hard-delete (vertices + touching edges, episodes via the sanctioned WORM bypass, then drops the vector index so retrieval self-heals). Both tenant-scoped, optionally agent-scoped. `POST /v1/forget` exposes soft-delete (write-only); `purge` stays an ops callable. **ABAC**: `store`/`step`/`forget` require `access_level == "write"` (else `403`); `retrieve` allows read — the Vercel adapter already declares `write`/`read` correctly (3.5b). **Step 5 (security) is now complete (5a/5b).** Next: Step 6 (observability).
>
> **Rev 19 updates (from Step 6a):** **observability facade + core instrumentation** (§18). `telemetry/`: a `MemoryMetrics` event emitter (`on`/`emit`/`clear` + singleton `metrics`) and `span(name, **attrs)` — an OTEL span via the otel-api, **no-op without a configured provider** (CI needs no collector). `retrieve()` emits a `memory.retrieve` span + `retrieval` event (`duration_ms`/`results_k`/`tokens_injected`/`mode`) and **wraps the impl in try/except → empty result + `degraded` event** (this completes the core-side §15 read-degradation the API lacked — a memory fault never breaks the turn). `store()` emits a `memory.write` span + `write` event; the worker emits `write{dead_lettered:true}`. OTEL *meter instruments* deferred (span attributes + emitter payloads carry values). Next: Step 6b (lifecycle metrics: decay/consolidation/conflict + cache-hit-rate + graph gauges).
>
> **Rev 20 updates (from Step 6b — Step 6 complete):** the remaining §18 metrics are wired through the 6a facade. Counters: `decay_sweep` → `decay{pruned}`, `run_dream_state` → `consolidation{promoted,superseded,cleared,breaker_tripped}`, write-time detection → `conflict{detected}`. `QueryCache` now tracks hits/lookups + `hit_rate` and emits `cache{hit,hit_rate}` (a dedicated *embedding* cache + its hit rate remains a future feature). New `stats(db, tenant_id)` returns per-tenant counts + emits a `graph` gauge, exposed via `GET /v1/stats` — which also implements the **§19 `stats`** contract that was never built. **Step 6 (observability) is now complete (6a/6b).** Next: Step 7 (hardening + ops).
>
> **Rev 21 updates (from Step 7a):** the **schema migration runner** is in (§6 startup step 2). `schema/migrations.py`: `Migration(version, name, apply)` + a `MIGRATIONS` registry + `run_migrations(db)` — ensures a `meta` collection, reads the applied `schema_version`, applies pending migrations in version order exactly once, and records the new version. `ensure_schema` runs migrations after the idempotent baseline. **Architecture:** `ensure_schema` stays the idempotent baseline; the runner owns versioned deltas going forward (matching how the schema actually evolved — additively). `MIGRATIONS` is empty at v1; future schema changes register a `Migration`. (`meta` collection named without the leading underscore — ArangoDB reserves `_*`, as with `failed_writes`.) Next: Step 7b (ops CLI: vector:rebuild, embeddings:migrate, dead-letter replay).
>
> **Rev 22 updates (from Step 7b):** the **ops CLI** is in (`python -m arango_memory.ops <cmd>`) — admin/destructive maintenance kept off the HTTP API. Importable functions + a thin argparse dispatch (env-driven connection, like `check.py`): `vector-rebuild` (`rebuild_vector_index` = drop + recreate the Faiss index), `embeddings-migrate` (`migrate_embeddings` = re-embed only docs whose `embedding_version` is stale across memories + entities, then rebuild — idempotent), `replay` (`replay_dead_letters` = re-enqueue + drain `failed_writes`). Next: Step 7c (full LoCoMo benchmark runner).
>
> **Rev 23 updates (from Step 7c — v1 COMPLETE):** the **full benchmark runner** is in (`eval/benchmark.py`). `run_benchmark` aggregates per-sample evals into overall + per-category metrics — Recall@k, mean token-F1, mean tokens-injected, and a per-category (**Deducible**) breakdown — and `_evaluate_targets` compares them to the §23 targets (token-F1 ≥ 0.65, tokens/turn ≤ 1500, recall floor). CLI `python -m arango_memory.eval.benchmark <dataset> [--mode] [--k]` prints a report and **exits nonzero below targets** (nightly-gate-capable). Real LoCoMo data is a manual BYO run (large/externally-licensed); tested on the smoke slice. Hallucination Rate / Noise Reduction Rate need a generated-answer + judge harness — out of scope (future). **This completes the v1 build sequence (Steps 0–7).** Remaining items are roadmap/deferred only: Step 3e (GLiNER/Haiku extraction tier), Step 3.5c (full Next.js chat UI), and the v2 adapters (§21).
>
> **Rev 24 updates (v2 — MCP server):** the first v2 adapter (§21) is in — a Python **FastMCP** server in the core package (`arango_memory/mcp/`) wrapping the core's `/v1` HTTP API as MCP tools (`store`/`search`/`record_step`/`list_steps`/`forget`/`stats`) for Claude Desktop / Cursor / Windsurf. Tool logic (`tools.py`) is `mcp`-free and tested against the core via a `TestClient`; `server.py` registers tools on a FastMCP app with an httpx client to `ARANGO_MEMORY_CORE_URL`; run via `python -m arango_memory.mcp` (stdio). `mcp` is an optional extra (+ dev for CI). `get_entity`/`list_entities`/`seed` tools remain future roadmap (need new core endpoints). Next (your call): LangChain/CrewAI adapters, Step 3e, or Step 3.5c.
>
> **Rev 25 updates (v2 — full §19 entity API):** the previously-unbuilt §19 operations are in. `entity_api.py`: `get_entity(entity_id, tenant)` → entity + `relates_to` neighbours; `list_entities(tenant, agent?, label?)`; `seed(profile {role, domain, preferences})` → one seed entity per item via UPSERT (source=`seed`, confidence 0.6) that **never clobbers observed facts** (§11). All projections **exclude embeddings** (§17). Endpoints: `GET /v1/entity`, `GET /v1/entities`, `POST /v1/seed` (write-only). The **MCP server gains the matching `get_entity`/`list_entities`/`seed` tools** (now 9) — closing the §21 tool-surface gap. Next (your call): LangChain/CrewAI adapters, Step 3e, or Step 3.5c.
>
> **Rev 26 updates (v2 — LangChain/LangGraph adapter):** the second v2 adapter (§21) is in — **in-process Python** (no HTTP hop, unlike the Vercel TS client), living in the core package at `arango_memory/langchain/` (a documented deviation from the spec's `adapters/langchain` path, mirroring the MCP placement). Three primitives over `retrieve`/`store`/`record_step`: **`ArangoMemoryRetriever`** (`BaseRetriever` → relevant memory as `Document`s + `assemble_context()` for the token-budgeted block); **`ArangoChatMessageHistory`** (`BaseChatMessageHistory` — persists a session transcript through the durable core and rebuilds it on read; `clear()` soft-deletes via `invalid_at`, preserving episode WORM); **`ArangoMemoryNode`** (LangGraph `recall`/`remember` nodes — retrieve+inject a `[MEMORY CONTEXT]` `SystemMessage`, then store turns + capture completed `tool_call`/`ToolMessage` pairs as procedural memory, deduped + chained via `prev_step_key`). All projections **exclude embeddings** (§17). One small **additive** core change: `store(message_type=…)` tags the episode with the chat role (default `None`; never touches redaction/hashing/embedding) so the transcript reconstructs faithfully. New `langchain` optional extra (`langchain-core` + `langgraph`, also in `dev`); tests exercise the real classes + a live `StateGraph` against testcontainers with the Fake providers (keyless). Next (your call): CrewAI adapter, Step 3e, or Step 3.5c.
>
> **Rev 27 updates (v2 — CrewAI adapter):** the last §21 adapter is in — in-process Python at `arango_memory/crewai/`, a **shared-crew memory store** realising the **G-Memory 3-tier (§14) purely via `agent_id` namespacing** (no core change): `interaction` (an agent's private memory), `query` (`<crew_id>::query`, shared read/write across the crew), `insight` (`<crew_id>::insight`, shared + read-only here — only the Dream State path writes it). **`ArangoCrewStorage`** speaks the stable text-based contract — `save(value, metadata)` / `search(query, limit, score_threshold)` / `reset()` — mapping straight onto the core's hybrid `retrieve()` (raw text in, so BM25/graph fusion is preserved); `reset()` is a `forget` soft-delete; results **exclude embeddings** (§17). **`crew_memory(...)`** builds all three tiers for one agent. Per the chosen strategy the logic is **crewai-free and tested directly** (testcontainers + Fakes, keyless); **`to_crewai_storage()`** is a thin `crewai.Storage` shim (lazy `crewai` import) wired via `Crew(external_memory=ExternalMemory(storage=…))`, tested against a stubbed `crewai` module so CI stays light. New `crewai` optional extra (**not** in `dev` — deliberately out of CI). **All §21 adapters (MCP, LangChain/LangGraph, CrewAI) are now shipped.** Remaining roadmap: Step 3e, Step 3.5c.
>
> **Rev 28 updates (Step 3e — heavy extraction tier):** the deferred §8 Stage 2 tiers are in, all behind the existing `Extractor` Protocol (additively extended with `extract_relations`). **`GlinerExtractor`** (tier B) — GLiNER zero-shot NER + GLiREL **typed relations**, with the NER model and relation function **injectable** so the logic is tested with deterministic fakes (torch stays out of CI). **`HaikuExtractor`** (tier C) — entities + typed relations as JSON via a `Generator`, **keyless in CI via `FakeGenerator`**, one cached LLM call serving both `extract`/`extract_relations` (§16). **`LayeredExtractor`** — the A→B→C chain: spaCy → GLiNER fill → escalate to Haiku **only** when the cheaper tiers yield `< extraction_escalate_below` entities. **Typed relations now populate the graph:** `write_entities` writes the typed `relates_to` label where an extractor produced one, **falling back to co-occurrence `associated_with`** for untyped pairs (typed written first; same idempotent edge key). **Explicit `valid_time`** (closing the Step 4→3e deferral): a deterministic, keyless regex parser (`ingest/temporal.py` — ISO dates, "Month [DD,] YYYY", bare years) sets `valid_time`/`valid_time_explicit=True` on entities + their typed edges when the text states *when* a fact holds (§4). New config: `extraction_provider` gains `gliner|haiku|layered`, plus `gliner_model`/`gliner_entity_labels`/`relation_labels`/`extraction_escalate_below`. `glirel` added to the `extraction` extra (**not** `dev` — CI stays torch-free); 10 keyless tests. **Remaining roadmap: Step 3.5c only.**
>
> **Rev 29 updates (Step 3.5c-0 — Memory Dungeon scaffold):** the §3.5c reference app is **Memory Dungeon** (`examples/dungeon/`) — a text-adventure where the world persists and the NPCs lie (the lie-catching mechanic = bi-temporal supersession + conflict detection made playable; the map = the knowledge graph). Scope **Standard** (3.5c-0→3); host decided later (built against `CORE_URL` + `docker-compose.yml`). **3.5c-0 (scaffold)** ships: a Next.js 15 App Router app (TS strict, React 19) with the **locked dual theme** ported from the design-of-record mockup (`docs/mockups/dungeon-ui.html`) — Geist + Fraunces, candle-lit **dark** + Vercel-white **light** with a persisted toggle + no-flash theme script; a typed server-side core client (`lib/core.ts`) over `/v1`; `/api/health` + a live footer status; `docker-compose.yml` (ArangoDB Enterprise + the Python core via the existing `core/Dockerfile`). A new **`dungeon` CI job** (build + typecheck) joins `core`/`adapter`. Verified: `next build` + `tsc` clean. Next: 3.5c-1 (the playable loop — `streamText` + `arangoMemory()` + `useChat` + `look`/`move`/`take`).

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Architecture Decisions (Rev 2)](#2-architecture-decisions-rev-2)
3. [Monorepo Structure](#3-monorepo-structure)
4. [Core Concepts](#4-core-concepts)
5. [Graph Schema](#5-graph-schema)
6. [ArangoDB Infrastructure](#6-arangodb-infrastructure)
7. [Vector Index Tenancy and Cold Start](#7-vector-index-tenancy-and-cold-start)
8. [Ingestion Pipeline](#8-ingestion-pipeline)
9. [Retrieval Pipeline](#9-retrieval-pipeline)
10. [Lite vs Full Enrichment Modes](#10-lite-vs-full-enrichment-modes)
11. [Memory Lifecycle](#11-memory-lifecycle)
12. [Conflict Resolution](#12-conflict-resolution)
13. [Consolidation and Dream State](#13-consolidation-and-dream-state)
14. [Multi-Agent and Multi-Tenant Design](#14-multi-agent-and-multi-tenant-design)
15. [Write Durability and Graceful Degradation](#15-write-durability-and-graceful-degradation)
16. [Operational Cost Model](#16-operational-cost-model)
17. [Security Layer](#17-security-layer)
18. [Observability](#18-observability)
19. [The Core API (language-agnostic contract)](#19-the-core-api-language-agnostic-contract)
20. [Vercel AI SDK Adapter (v1)](#20-vercel-ai-sdk-adapter-v1)
21. [Deferred Adapters (v2)](#21-deferred-adapters-v2)
22. [Testing and Eval Harness](#22-testing-and-eval-harness)
23. [Benchmarking Strategy](#23-benchmarking-strategy)
24. [Build Sequence](#24-build-sequence)
25. [Development, Tooling & Infrastructure](#25-development-tooling--infrastructure)

---

## 1. Purpose and Scope

### What This Is

A universal agentic memory backend built on ArangoDB, giving AI agents persistent, relational memory across sessions — combining the semantic richness of a knowledge graph, the fuzzy recall of vector search, and the temporal precision of bi-temporal edge tracking.

### Why ArangoDB

The only production database combining documents, graph, full-text (BM25), and vector search in a single query language (AQL):

| Memory Need | ArangoDB Primitive |
|---|---|
| Fuzzy semantic recall | Faiss IVF vector index |
| Keyword / phrase search | ArangoSearch + BM25 |
| Relational reasoning | Named graph + AQL traversal |
| Temporal fact tracking | Edge documents with bi-temporal fields |
| Tenant isolation | Collection-level scoping via AQL bind vars |

### v1 Scope (decided Rev 2)

**v1 ships the Python core + the Vercel AI SDK adapter only.** This matches the project's stated end goal — an agentic memory plugin for the Vercel agent SDK. LangChain/LangGraph, CrewAI, and the MCP server are explicitly deferred to v2. The core API is kept language- and adapter-neutral so v2 adapters are additive, not refactors.

### What This Is Not

- Not a general-purpose RAG/document-chunking system
- Not a vector database replacement — graph traversal is first-class
- Not opinionated about the LLM

### Research Grounding

Grounded in: Neo4j agent-memory, Memgraph, FalkorDB, RushDB, Kumiho, SCM, GAM, G-Memory, AriGraph, Letta/MemGPT, MemoryBank, Graphiti/Zep, A-Mem, ALMA, RAGA, Memoria. NotebookLM: "Agentic knowledge memory graph" (40 sources).

---

## 2. Architecture Decisions (Rev 2)

These three decisions, made after a critical reassessment of rev 1, shape the rest of this document.

### Decision 1 — Python-first core, thin TypeScript client

The core (schema, ingestion, retrieval, consolidation, decay) is **Python**. Rationale:

- The extraction pipeline (spaCy, GLiNER2, GLiREL) is Python-only.
- The broader memory ecosystem we reference (Graphiti, mem0, research systems) is Python-first.
- The Vercel adapter's job is thin — intercept, retrieve, inject, write — and is naturally a client over a boundary. Keeping it thin also helps latency.

The **Vercel adapter is a thin TypeScript client** that calls the Python core across a transport boundary:

- **Local dev:** the Python core runs as a sidecar process exposing a local HTTP API; the Next.js app calls `http://localhost:<port>`.
- **Production:** the Python core is deployed as a service (container) the middleware calls over HTTP.

This isolates all cross-language complexity at one well-defined seam. It is also a clean stepping-stone to a fully language-agnostic service core later, if multi-language crews demand it.

### Decision 2 — v1 is Vercel-only

See §1. Core + Vercel adapter. LangChain/CrewAI/MCP deferred to v2 (§21).

### Decision 3 — Walking skeleton first, then harness, then thicken

Build the thinnest end-to-end loop before any breadth. Prove the architecture (and the TS↔Python seam, and the latency/cost envelope) serves a single real turn, then add the test/eval harness, then thicken each layer. See §24.

---

## 3. Monorepo Structure

```
arango-agentic-memory/
├── docs/
│   ├── DESIGN.md          ← this file
│   ├── api.md             ← core API reference (future)
│   ├── ops.md             ← operations runbook (future)
│   └── adapters/          ← per-adapter guides (future)
├── core/                  ← Python core (the heart of the system)
│   ├── pyproject.toml     ← uv-managed
│   ├── Makefile           ← dev tasks; bakes in relocated venv + PYTHONPATH (§25)
│   ├── Dockerfile         ← uv-based image (PYTHONPATH=/app/src)
│   └── src/arango_memory/
│       ├── client.py      ← ArangoDB client + connection abstraction (TLS, targets)
│       ├── config.py      ← env-driven Settings (target, mode, tls, budgets)
│       ├── models.py      ← record helpers (idempotency_key, timestamps)
│       ├── check.py       ← connection/round-trip probe CLI (both targets)
│       ├── schema/        ← collection/view/index definitions, migrations
│       ├── ingest/        ← PII redaction, extraction, prospective indexing, writes
│       ├── retrieve/      ← HyDE, hybrid search, fusion, reranking, budget
│       ├── lifecycle/     ← decay, consolidation, Dream State worker
│       ├── api/           ← local/service HTTP API (FastAPI) — the boundary
│       └── telemetry/     ← OpenTelemetry instrumentation
├── packages/
│   └── vercel/            ← thin TypeScript client (LanguageModelV2Middleware)
│       └── package.json   ← pnpm (npm in practice; pnpm not installed locally)
├── examples/
│   └── vercel-agent/      ← reference Next.js agent app: demo + simulation SUT (§22)
├── sim/                   ← deterministic agentic simulation harness (§22)
├── .github/workflows/     ← gitleaks + core CI (lint/type/test) (§25)
├── docker-compose.yml     ← ArangoDB (Enterprise) + Python core sidecar for local dev
├── .env.example
├── .pre-commit-config.yaml ← gitleaks pre-commit hook (§25)
└── README.md

# Deferred to v2 (not created in v1):
#   packages/mcp/          ← MCP server
#   adapters/langchain/    ← Python LangChain/LangGraph adapter
#   adapters/crewai/       ← Python CrewAI adapter
```

**Package managers:** `uv` for Python, `pnpm`/`npm` for TypeScript.
**Publishing (v1):** `arango-memory` (PyPI, the core), `@arango-memory/vercel` (npm).
**Dev venv:** lives at `$HOME/.venvs/arango-memory` (outside iCloud), driven by the `core/Makefile` — see §25.

---

## 4. Core Concepts

### Memory Types

| Type | Stores | Lifetime | ArangoDB Home |
|---|---|---|---|
| **Working** | Current session context, active entities | Session TTL | `memories` (TTL index) |
| **Episodic** | Past interactions, events, observations | Decays over time | `memories` |
| **Semantic** | Facts, entities, domain knowledge | Permanent (soft-deprecate only) | `entities` + graph |
| **Procedural** | Tool traces, reasoning patterns | Permanent | `steps` |

### The Episode as Provenance Anchor

Every memory traces back to a raw **episode** (the original input). Episodes are write-once (WORM). When derived facts are later deprecated/contradicted, the source episode remains for audit.

```
Episode (raw input)
  └── produces → Entity nodes (semantic)
  └── produces → Interaction nodes (episodic)
  └── produces → Step nodes (procedural)
```

### Bi-Temporal Edges

Every edge carries two time dimensions:

- `valid_time` — when the fact is/was true in the real world
- `ingestion_time` — when the system learned about it

**`valid_time` derivation (resolved Rev 2):** The extraction stage attempts to extract an explicit real-world time from the source text (e.g., "since March", "last year"). If found, `valid_time` is set to that. If not found, `valid_time` defaults to `ingestion_time`. A boolean `valid_time_explicit` records which path was taken, so downstream reasoning knows whether the time is asserted or assumed.

---

## 5. Graph Schema

### Collections (Document)

```
episodes          WORM. Raw input provenance. Never modified.
                  Fields: episode_id, idempotency_key (unique), content,
                          source_type, agent_id, tenant_id, session_id,
                          ingested_at

memories          Episodic and working memory.
                  Fields: memory_id, idempotency_key (unique), text,
                          embedding, embedding_model, embedding_version,
                          type (working|episodic), strength, created_at,
                          accessed_at, expires_at (TTL), source_episode_id,
                          agent_id, tenant_id, schema_version,
                          prospective_queries (array, full mode only)

entities          Semantic nodes — the long-term knowledge graph.
                  Fields: entity_id, label (Person|Organization|Location|
                          Event|Object|Concept), name, summary,
                          mention_count, confidence, source (observed|seed),
                          valid_time, valid_time_explicit, ingestion_time,
                          invalid_at, embedding, embedding_model,
                          embedding_version, agent_id, tenant_id,
                          schema_version

steps             Procedural memory — tool traces and reasoning patterns.
                  Fields: step_id, tool_name, arguments, outcome
                          (success|failure), pattern_summary, use_count,
                          agent_id, tenant_id

sessions          Conversation/task sessions.
                  Fields: session_id, agent_id, tenant_id, started_at,
                          ended_at, topic_embedding

agents            Agent identities. Fields: agent_id, tenant_id, name, role
tenants           Tenant isolation root. Fields: tenant_id, name, config
_meta             Single doc. schema_version for migration runner.
```

**Idempotency (resolved Rev 2):** `episodes` and `memories` carry an `idempotency_key` with a unique persistent index. The key is a hash of `(tenant_id, agent_id, session_id, content, turn_index)`. Re-running a turn or retrying a failed write cannot create duplicates. Entities remain deduped by UPSERT on natural key `(name, label, tenant_id)`.

### Edge Collections

```
produced_by    entity/memory/step → episode (provenance)
mentions       memory → entity
relates_to     entity ↔ entity (caused_by | occurred_during | subtopic_of | associated_with)
Supersedes     entity → entity (contradiction resolution, new→old)
TOUCHED        step → memory (reasoning trace → triggering message)
TRANSITION     step → step (procedural workflow sequencing)
memory_of      session → memory
appeared_in    entity → session
owned_by       memory/entity/step → tenant
```

### Edge Fields (all edges)

```json
{
  "valid_time":          "ISO8601 — when fact is/was true",
  "valid_time_explicit": "bool — asserted in source vs defaulted",
  "ingestion_time":      "ISO8601 — when system learned this",
  "invalid_at":          "ISO8601 | null — soft-deprecation, never deleted",
  "weight":              "float 0.0–1.0 — EWA-computed relevance",
  "relationship":        "string — typed label",
  "_rev":                "ArangoDB native — optimistic locking"
}
```

**Idempotency implementation (Step 0):** `idempotency_key = sha256(tenant_id ⨂ agent_id ⨂ session_id ⨂ turn_index ⨂ content)`. For `episodes`/`memories` this hash is used as the document `_key` (memory uses `{key}-mem`), and inserts use `overwrite_mode="ignore"` — so a retried/duplicate write is a no-op rather than a duplicate. A unique persistent index on `idempotency_key` backs this.

### ArangoSearch View

The view indexes memory `text` (and entity `name`/`summary`) for BM25, plus
`tenant_id`/`agent_id` with the `identity` analyzer so tenant scoping is applied
inside the `SEARCH` clause (not as a post-filter). Step 0 links `memories`;
`entities` links are added when semantic retrieval lands.

```json
{
  "name": "memory_search_view",
  "type": "arangosearch",
  "links": {
    "memories": { "fields": { "text":      { "analyzers": ["text_en"] },
                              "tenant_id": { "analyzers": ["identity"] },
                              "agent_id":  { "analyzers": ["identity"] } } },
    "entities": { "fields": { "name":    { "analyzers": ["text_en"] },
                              "summary": { "analyzers": ["text_en"] } } }
  },
  "primarySort": [{ "field": "ingestion_time", "direction": "desc" }],
  "commitIntervalMsec": 1000,
  "consolidationIntervalMsec": 10000
}
```

---

## 6. ArangoDB Infrastructure

### Image & Version (decided Rev 3)
**`arangodb/enterprise:3.12.9.1`.** Findings from Step 0:
- The **3.12.9.x line is published only as `arangodb/enterprise`** — the Community image (`arangodb/arangodb`) tops out at `3.12.4.3` on Docker Hub.
- The Enterprise image **starts in evaluation mode without a license** ("ready for business"); set `ARANGO_LICENSE_KEY` (via `.env`) for licensed/full use. Never commit the key.
- **Vector-index startup flag renamed** `--experimental-vector-index` → `--vector-index` in 3.12.9 (the old name still works with a deprecation warning). Compose uses `--vector-index=true`; startup logs confirm the `VectorIndex` column family is created.
- 3.12.9+ is the target because it gives vector-index auto-training (create index before data load).

### Connection Targets (decided Rev 3)
Two configurable targets, selected entirely via environment — same code path for both:

| `ARANGO_TARGET` | Where | Auth | TLS |
|---|---|---|---|
| `local` (default) | Docker container (`docker-compose`) | basic (root/password) | plain http |
| `arangograph` | Arango's managed cloud | basic **or** bearer/JWT token | https, `ARANGO_TLS_VERIFY=true` |

- `client.py` passes `verify_override` (from `ARANGO_TLS_VERIFY`) and `request_timeout` to the ArangoDB client; chooses bearer-token auth if `ARANGO_BEARER_TOKEN` is set, else basic.
- **Connection probe:** `python -m arango_memory.check` connects, bootstraps schema, runs a real store→retrieve round-trip under an isolated `__healthcheck__` tenant, then cleans up. Works against either target; used to validate cloud connectivity.
- **ArangoGraph caveat:** it's managed, so the `--vector-index` startup flag is **not user-settable** there — confirm vector-index availability on the chosen tier before relying on it for production (the BM25 path works regardless; §7).

### Startup Sequence
1. Connect, verify database (auto-create if missing)
2. Migration runner — check `_meta.schema_version`, apply pending scripts
3. Ensure collections exist
4. Ensure ArangoSearch view exists
5. Ensure vector indexes exist (create + await training trigger)
6. Verify graph definition

---

## 7. Vector Index Tenancy and Cold Start

*(Resolved Rev 2 — was a rev 1 blocker.)*

### Tenancy: shared index, logical isolation

A **single shared Faiss IVF index** per embedding field (`memories.embedding`, `entities.embedding`), with **tenant isolation enforced logically** at query time via `FILTER doc.tenant_id == @tenant_id`. Rationale:

- Per-tenant physical indexes are useless for new/small tenants until they reach the `nLists` training threshold (≥256 docs) — an unacceptable cold-start cliff.
- A shared index trains on the aggregate corpus, so every tenant — including brand-new ones — gets a well-trained index immediately.
- Centroids are derived from vectors only; combined with strict query-time `tenant_id` filtering and the security layer (§17), no tenant can retrieve another's documents.

**Privacy note:** centroid positions are influenced by aggregate data but expose no document content. For deployments requiring physical isolation (regulated tenants), a per-tenant-database deployment is the escape hatch — documented in `ops.md`, not the default.

### Cold-start ramp

Until the shared index is trained (corpus < `nLists` docs in a fresh deployment), retrieval **falls back to BM25-only** via ArangoSearch, which needs no training. The retrieval pipeline detects `vector_index.trainingState != "ready"` and degrades gracefully to keyword + graph expansion. Vector search activates automatically once training completes. This is the same degradation path used when the vector index is unavailable (§15).

### nLists tiers

| Tier | nLists | Suitable for |
|---|---|---|
| `small` (default) | 256 | ≤ 300K documents |
| `medium` | 1024 | ≤ 5M documents |
| `large` | 4096 | ≤ 75M documents |

`nProbe` defaults to 10, configurable per query. Rebuild threshold: 10× growth from training size (`vector:rebuild` ops command).

---

## 8. Ingestion Pipeline

Five stages before the graph is touched.

```
Raw Input
  │
  ▼
Stage 1: PII Redaction
  Regex for credentials/keys/SSN/PAN; Haiku pass for complex PII (full mode).
  Redacted content is what gets persisted. Original never stored.
  │
  ▼
Stage 2: Entity Extraction (multi-stage, NOT LLM-only)
  A — spaCy NER: fast, free, ~80% of entities
  B — GLiNER2 / GLiREL: zero-shot NER + relationship extraction
  C — claude-haiku-4-5: fallback for ambiguous cases only
  Also extracts valid_time when explicitly present in text (§4).
  │
  ▼
Stage 3: Write-Time Conflict Detection
  Cosine similarity of each new entity vs existing:
    > 0.9  → duplicate → merge via UPSERT
    0.6–0.9 → likely contradiction → flag for Dream State review
    < 0.6  → new entity → create
  │
  ▼
Stage 4: Prospective Indexing  [FULL MODE ONLY]
  Generate 2–3 hypothetical future queries this memory answers (Haiku),
  store in memories.prospective_queries, include in the search index.
  Skipped entirely in lite mode (§10).
  │
  ▼
Stage 5: Graph Write (concurrent-safe, idempotent, durable)
  - Episode: INSERT ... with idempotency_key (unique) — dedupe on retry
  - Entities: UPSERT by (name, label, tenant_id)
  - Memory: INSERT with idempotency_key; TTL if working
  - Edges: INSERT; _rev optimistic locking for updates;
           stream transaction for atomic multi-step writes
  - Writes flow through the durable write path (§15), not fire-and-forget
```

### Concurrency Strategy

| Operation | Strategy |
|---|---|
| Entity create | AQL `UPSERT` — idempotent |
| Edge update | `_rev` optimistic locking + retry |
| Multi-step atomic write | ArangoDB stream transaction |
| Dream State worker | CAS `consolidation_lock` flag — one worker at a time |
| Duplicate suppression | `idempotency_key` unique index on episodes/memories |

### Embedding (pluggable)

```python
class Embedder(Protocol):
    model: str
    version: str
    dimensions: int
    async def embed(self, text: str) -> list[float]: ...

# Default: OpenAI text-embedding-3-small (1536 dims)
```

---

## 9. Retrieval Pipeline

> **Step 2 status:** Stages 1 (adaptive gate) and 2 (HyDE) are implemented (full mode, Step 2b); stages 3 (vector + BM25), 5 (RRF + MMR), and 6 (tiered token budget) are implemented, with vector self-healing to BM25 on cold start (§7). Stage 4 (graph expansion) is implemented (Step 3b): a relates_to traversal from the top hits' entities, fused as a third RRF signal.

Six stages. **Latency note:** stages 1–2 involve LLM calls and are part of the *augmented* latency budget, not the *core* retrieval budget (§23). Lite mode skips them entirely (§10).

```
Query Text
  │
  ▼
Stage 1: Adaptive Gate (fast-slow)         [FULL MODE ONLY]
  Quick draft (no memory). High confidence → skip retrieval.
  Low confidence → continue. Result cached per (query hash).
  │
  ▼
Stage 2: HyDE                              [FULL MODE ONLY]
  Generate hypothetical answer (Haiku), embed that not the question.
  Hypothetical+embedding cached per (query hash) to avoid repeat calls.
  │
  ▼
Stage 3: Parallel Search                   [CORE — always on]
  A) Faiss IVF vector search (if trained; else skipped — see §7)
     APPROXIMATE_NEAR_NEIGHBORS(doc.embedding, @qVec, @nProbe), top-20
  B) ArangoSearch BM25, top-20
  Both filtered: tenant_id, agent_id, invalid_at == null
  In lite mode the query embedding is used directly (no HyDE).
  │
  ▼
Stage 4: Graph Expansion                   [CORE]
  1–2 hop traversal from seed entities (relates_to, mentions). 3 hops max.
  │
  ▼
Stage 5: Fusion + Reranking                [CORE]
  RRF merge (vector + BM25) → recency/access boost → MMR diversity.
  Hard cap k ≤ 10.
  │
  ▼
Stage 6: Context Assembly (token budget)   [CORE]
  Tiered (configurable maxMemoryTokens, default 1500):
    working 400 · episodic 700 · semantic 300 · reasoning 100
  Sort by score within tier; unused budget rolls up. tiktoken counting.
```

---

## 10. Lite vs Full Enrichment Modes

*(New in Rev 2 — directly addresses the latency and cost concerns.)*

A single `mode` setting controls which expensive enrichments run. This is the primary lever for the latency/cost envelope.

| Capability | Lite | Full |
|---|---|---|
| Adaptive gate (draft LLM call) | ❌ | ✅ |
| HyDE (hypothetical answer LLM call) | ❌ | ✅ |
| Prospective indexing (write-time LLM calls) | ❌ | ✅ |
| Haiku extraction fallback | ❌ (spaCy/GLiNER2 only) | ✅ (LayeredExtractor, Step 3e) |
| Vector + BM25 + graph + RRF + MMR | ✅ | ✅ |
| Tiered token budget | ✅ | ✅ |

- **Lite mode** has **zero LLM calls in the retrieval hot path** and minimal calls at write time → predictable low latency and cost. It is the default for the walking skeleton and a supported production mode for latency-sensitive deployments.
- **Full mode** enables all recall-boosting enrichments for quality-sensitive deployments, with caching to amortize repeat LLM calls.

Default: **lite**. Opt into full explicitly.

---

## 11. Memory Lifecycle

### Working Memory
- `type: "working"`, `expires_at` = session end (TTL index auto-deletes)
- Max 7 active episodes (SCM model); overflow compresses oldest to episodic

### Episodic Memory
- Promoted from working at session end or topic shift
- Strength: `novelty × task_relevance × frequency × exp(-λ × time_since_access)`
- `accessed_at` resets on retrieval (spaced repetition)
- Scheduled decay job soft-deprecates below threshold (`invalid_at` set, never deleted)

### Semantic Memory (Entities)
- Promoted via consolidation (§13); `mention_count` increments
- Never auto-deleted — only soft-deprecated via `Supersedes` when contradicted
- `confidence`: 1.0 observed, 0.6 seeded

### Procedural Memory (Steps)
- Written at tool completion: name, args, outcome
- TOUCHED edge links step → triggering message; `use_count` on reuse

### Cold Start
```python
await memory.seed(
    tenant_id=..., agent_id=...,
    profile={"role": "...", "domain": "...", "preferences": [...]},
)
# Writes directly to entities; source="seed", confidence=0.6
# Overwritten easily by observed facts (confidence=1.0)
```
Episodic cold start is accepted as natural; semantic cold start is mitigated by `seed()` and the shared-index strategy (§7).

---

## 12. Conflict Resolution

```
New fact B contradicts existing fact A
  ├── Write-time detection (sim 0.6–0.9) → flag for Dream State
  ▼
Dream State confirms contradiction:
  1. Insert B (valid_time, ingestion_time)
  2. Supersedes edge B → A
  3. Move `latest` tag pointer to B
  4. A.invalid_at = now()  (excluded from retrieval)
  5. A retained for audit — never hard-deleted
```

- **EWA weights:** newer `ingestion_time` → higher edge weight; agent prioritizes most recent confirmed fact.
- **Deterministic override:** human-edited config wins over LLM-extracted facts (checked first at retrieval).

---

## 13. Consolidation and Dream State

### Trigger: GAM Semantic Boundary
Consolidation does **not** run per turn. After each turn, embed the turn's topic and compare to the session's running topic embedding; if cosine < 0.7 (topic shift), flush working buffer to episodic and run a consolidation check.

### Consolidation Check
Per referenced entity: if `mention_count >= threshold` (default 5) → queue for Dream State; else update strength and leave episodic.

### Dream State Worker (async, background)
```
For each queued entity:
  1. Gather all episodic memories mentioning it
  2. Haiku review: stable fact? contradicts existing? distilled summary?
  3. Promote → UPSERT entity + produced_by/mentions edges
  4. Contradicts → Supersedes edge + soft-deprecate old

Circuit breaker: if >50% of entities flagged for deprecation in one run,
  halt, alert, require manual review (poisoning safeguard).
```

---

## 14. Multi-Agent and Multi-Tenant Design

### Tenant Isolation
Every AQL query binds `@tenant_id`; no cross-tenant traversal. Embedding caches namespaced per tenant (timing-attack defense).

```aql
FOR e IN entities
  FILTER e.tenant_id == @tenant_id
     AND e.agent_id == @agent_id
     AND e.invalid_at == null
  ...
```

### Multi-Agent (G-Memory 3-Tier) — *schema-ready in v1, adapters in v2*
```
interaction_graph  agent-to-agent logs (private working+episodic per agent)
query_graph        meta-links between tasks/goals (shared, read by all)
insight_graph      distilled strategies (shared, written only by Dream State)
```
The schema supports this in v1; the CrewAI adapter that exercises it shipped in v2 (§21) — the three tiers are realised via `agent_id` namespacing within a tenant (`interaction` = the agent's own id; `query` = `<crew_id>::query`; `insight` = `<crew_id>::insight`), with no schema change.

---

## 15. Write Durability and Graceful Degradation

*(New in Rev 2 — closes the "fire-and-forget" correctness gap and the missing degradation story.)*

### Durable write path
Memory writes from the adapter are **asynchronous but durable**, not fire-and-forget:

- The adapter enqueues a write intent (idempotency-keyed) and returns immediately — the agent turn never blocks on memory.
- A core-side worker drains the queue and commits to ArangoDB with retry + exponential backoff.
- Persistent failures land in a **dead-letter** record (`failed_writes`; the leading underscore from earlier revs is dropped — ArangoDB reserves `_*` for system collections) for inspection/replay via `ops`.
- Because writes are idempotency-keyed, replays cannot duplicate.

For the walking skeleton, the "queue" may be in-process; production uses a durable queue (e.g., Redis/SQS) — the interface is identical.

### Graceful degradation (memory must never break the agent)
| Failure | Behavior |
|---|---|
| ArangoDB unreachable (read) | Retrieval returns empty context; agent runs memory-less; turn succeeds |
| ArangoDB unreachable (write) | Write intent queued/dead-lettered; agent turn unaffected |
| Embedder API error | Lite mode: degrade to BM25-only retrieval. Full mode: skip HyDE, fall back to query text |
| Vector index not trained | BM25-only retrieval (§7) |
| Core service unreachable (adapter) | Middleware passes through to the model with no memory; logs + metric |

Principle: **memory is an enhancement, never a dependency.** Every memory failure degrades to a working, memory-less turn.

---

## 16. Operational Cost Model

*(New in Rev 2 — makes per-turn LLM/embedding spend explicit.)*

### Per-turn operation count

| Operation | Lite | Full |
|---|---|---|
| Retrieval LLM calls (adaptive gate + HyDE) | 0 | 0–2 (cached) |
| Extraction LLM calls (Haiku fallback) | 0 | 0–1 |
| Prospective indexing LLM calls | 0 | 0–3 |
| Embedding calls (query + new memories) | 1–N | 1–N |
| Background (Dream State) LLM calls | amortized, not per-turn | amortized |

- **Lite:** ~1 embedding call on the hot path, no hot-path LLM calls. Predictable and cheap.
- **Full:** up to ~6 LLM calls per turn worst-case, mitigated by caching (HyDE/adaptive-gate keyed by query hash) and by extraction/prospective indexing being conditional.

### Controls
- `mode: lite | full` (primary cost lever, §10)
- `maxMemoryTokens` (caps injected context tokens)
- All background LLM work uses `claude-haiku-4-5`; the full agent model is reserved for user-facing generation (Kumiho pattern)
- Cost-relevant metrics emitted via OTEL (§18): `tokens_injected`, per-stage LLM call counts

---

## 17. Security Layer

### PII Redaction (at ingestion, before write)
Regex (keys/tokens/SSN/PAN) + Haiku pass for complex PII (full mode). Episodes store redacted content only.

### Embedding Security
Encrypted at rest; per-tenant cache namespacing; embeddings never returned in API responses (inversion defense).

### ABAC at Query Time
```python
results = await memory.retrieve(query, ctx=AccessContext(
    tenant_id=..., agent_id=..., access_level="read", session_id=...,
))
```

### Cascade Delete / Right to Be Forgotten
1. **Soft-delete (immediate):** set `invalid_at` on all tenant/user docs → removed from all retrieval surfaces; cache evicted.
2. **Physical purge (async):** hard-delete, rebuild vector index, clear full-text index, purge cached embeddings.

### WORM Episodes
`episodes` is INSERT-only — no UPDATE/DELETE via application code. Enforced at the client layer.

### Prompt Injection Defense
Dream State operates only on stored structured metadata, never raw user input. Raw input passes through extraction first; only structured output reaches consolidation.

---

## 18. Observability

OpenTelemetry spans + metrics. No built-in dashboard — users plug into their backend.

**Spans:** `memory.retrieve`, `memory.write`, `memory.consolidate`, `memory.decay`, `memory.embed`

**Metrics:**
```
memory.retrieval.duration_ms          histogram  (core vs augmented tagged)
memory.retrieval.tokens_injected      histogram  (key cost metric)
memory.retrieval.results_k            histogram
memory.retrieval.llm_calls            counter     (full mode cost)
memory.write.duration_ms              histogram
memory.write.dead_lettered            counter     (durability health)
memory.conflict.detected_count        counter
memory.consolidation.promoted_count   counter
memory.decay.pruned_count             counter
memory.graph.entity_count             gauge (per tenant)
memory.graph.episode_count            gauge (per tenant)
memory.embedding.cache_hit_rate       gauge
memory.degraded_turn                  counter     (memory-less fallbacks)
```

Programmatic: `MemoryMetrics.on("retrieval", handler)` event emitter.

---

## 19. The Core API (language-agnostic contract)

The Python core exposes a stable API consumed locally (in-process Python) and over HTTP (the boundary for the Vercel adapter and future v2 adapters). Keeping this contract neutral is what makes v2 adapters additive.

```
store(content, ctx)                  → ingestion pipeline, returns ids
retrieve(query, ctx, opts)           → retrieval pipeline, returns assembled context
get_entity(entity_id, ctx)           → entity + edges
list_entities(ctx, label?)           → filtered semantic entities
forget(target_id, ctx)               → soft-deprecate
seed(profile, ctx)                   → cold-start seed
stats(ctx)                           → graph health metrics

ctx = { tenant_id, agent_id, session_id?, access_level }
opts = { mode: lite|full, max_memory_tokens, n_probe, k }
```

HTTP surface (FastAPI) mirrors these 1:1 under `/v1/*`. Transport: local sidecar in dev, deployed service in prod.

---

## 20. Vercel AI SDK Adapter (v1)

### Package: `@arango-memory/vercel` (thin TypeScript client)

```typescript
import { wrapLanguageModel } from 'ai'
import { arangoMemory } from '@arango-memory/vercel'

const model = wrapLanguageModel({
  model: anthropic('claude-sonnet-4-6'),
  middleware: arangoMemory({
    coreUrl: process.env.ARANGO_MEMORY_CORE_URL, // Python core endpoint
    tenantId: session.userId,
    agentId: 'assistant',
    mode: 'lite',              // default; 'full' opts into enrichments
    maxMemoryTokens: 1500,
  }),
})

const result = await streamText({ model, prompt }) // drop-in
```

### Middleware type (corrected Rev 3)
`ai@5` exposes the middleware as **`LanguageModelV2Middleware`** (the lower-level
`LanguageModelV2CallOptions`/`LanguageModelV2Prompt` types come from
`@ai-sdk/provider@2`). The earlier "V4 / specificationVersion `v3`" note was from
preliminary docs and does not match the shipped `ai@5` API.

```
transformParams (BEFORE):
  → POST {coreUrl}/v1/retrieve (AbortController timeout, default 800ms)
  → inject assembled context as a leading system message
  → on core/network failure OR timeout: pass through with no memory (§15)

wrapGenerate / wrapStream (AFTER):
  → POST {coreUrl}/v1/store (non-blocking; Step 0 is best-effort fire-and-forget,
    upgraded to the durable queue in Step 3 — §15)
  → capture tool traces → procedural memory
  → return immediately
```

The adapter holds **no memory logic** — all intelligence lives in the Python core. It is a transport + injection shim.

---

## 21. Deferred Adapters (v2)

The schema and core API are designed to support these without refactor.

- **MCP server** ✅ **DONE** — implemented as a Python FastMCP server in the core package (`arango_memory/mcp/`, not `packages/mcp`), wrapping the core's `/v1` HTTP API. Tools (9): `store` / `search` / `record_step` / `list_steps` / `forget` / `stats` / `get_entity` / `list_entities` / `seed` — the full §19 surface. Run via `python -m arango_memory.mcp` (stdio); core URL from `ARANGO_MEMORY_CORE_URL`.
- **LangChain / LangGraph** ✅ **DONE** — implemented in-process (no HTTP hop) in the core package at `arango_memory/langchain/` (not `adapters/langchain` — mirrors the MCP placement). Three primitives over the core: `ArangoMemoryRetriever` (`BaseRetriever`), `ArangoChatMessageHistory` (`BaseChatMessageHistory`, durable + WORM-preserving `clear()`), and `ArangoMemoryNode` (LangGraph `recall`/`remember` nodes — retrieve+inject, store turns, capture tool steps). The modern surface replaces the deprecated `BaseMemory`. Requires the `langchain` extra (`langchain-core` + `langgraph`).
- **CrewAI** ✅ **DONE** — implemented in-process at `arango_memory/crewai/` (not `adapters/crewai`; mirrors MCP/LangChain placement). A shared-crew memory store exercising the G-Memory 3-tier (§14) via `agent_id` namespacing: `ArangoCrewStorage` (text-based `save`/`search`/`reset` over the core's hybrid retrieve) + `crew_memory()` (interaction/query/insight tiers) + `to_crewai_storage()` (lazy `crewai.Storage` shim for `Crew(external_memory=…)`). Storage logic is crewai-free + tested directly; the `crewai` extra is needed only for the shim.

---

## 22. Testing and Eval Harness

*(New in Rev 2 — sequenced immediately after the walking skeleton.)*

### Unit / integration
- **ArangoDB via testcontainers** — spin a real `arangodb/enterprise:3.12.9.1` instance per test session (evaluation mode, no license needed); no mocking the database. Pass `--vector-index=true` for tests that exercise vectors.
- Fixtures for tenants/agents/sessions; deterministic seed data.
- Contract tests for the core HTTP API (the TS↔Python seam) so the adapter and core can't drift.
- Imports resolve via `pythonpath=["src"]` (pytest config), independent of the editable install (§25).

### Eval harness (dev loop)
- Minimal **LoCoMo-style** runner: load multi-session conversations, ingest, query, score F1 / Recall@k / Deducible Score.
- Runs locally and in CI on a small fixed slice; full benchmark runs are manual/nightly.
- Lite vs full mode compared on the same slice to quantify the quality/cost trade-off.
- **Step 1 status:** the runner is implemented (`arango_memory.eval`) but **lite/BM25-only** and scored on a tiny hand-built smoke slice — it validates the ingest→index→retrieve→score *plumbing*, not real-data quality. Real-data quality is the job of the simulation harness below.

### Agentic simulation harness (real-data validation) — *requirement; lands as a milestone after Step 3 (§24)*

The smoke eval proves the pipeline runs; it does **not** prove the system serves a real agent well. The simulation harness is the robust, real-data validation of the end-to-end product: **an agentic application on Vercel that uses ArangoDB to capture both agentic *memory* and *actions*** (procedural tool traces), exercised over realistic multi-session workloads and scored against the §23 targets.

It has **two components** (both built):

1. **Deterministic simulation harness (`sim/`, CI-friendly).** Scripts realistic agent behavior — multi-session conversations *and* tool/action calls — directly against the Vercel adapter + Python core, with a **stubbed/recorded model** so runs are deterministic and need no API key. This is the regression gate that can run in CI on the real LoCoMo slice. Must assert:
   - **Memory recall** on the real LoCoMo slice (F1 / Recall@k / Deducible Score vs §23 targets), lite **and** full mode.
   - **Action/procedural memory**: tool traces are written as `steps` with `TOUCHED`/`TRANSITION` edges (§5, §11) and are retrievable/reused on later turns.
   - **Graceful degradation** (§15): core down → memory-less turn still succeeds; embedder/vector failures fall back per the degradation table.
   - **Multi-tenant isolation** holds under concurrent simulated agents.

2. **Reference Vercel agent app (`examples/vercel-agent/`, manual/nightly).** A small real Next.js app wrapping a live model with `arangoMemory()` (§20), driving genuine `streamText` turns through adapter → core → ArangoDB. Doubles as the project demo and as the realistic (non-deterministic) end-to-end check; closes the Step 0 deferred item ("a live end-to-end `streamText` turn through the seam — needs an example app + model key").

**Prerequisites** (why it's a post-Step-3 milestone): full mode (Step 2), procedural/tool-trace ingestion, and the durable write path (Step 3) must exist first. The full benchmark run completes at Step 7.

### CI
- Lint + type (ruff/mypy for Python, eslint/tsc for TS) — locally via `make ci`
- Unit + integration (testcontainers)
- Eval smoke (small slice, regression gate on F1)
- Secret scanning (gitleaks) already runs server-side on every push/PR (§25)

---

## 23. Benchmarking Strategy

### Benchmarks
| Benchmark | Tests |
|---|---|
| LoCoMo / LoCoMo-Plus | Long-term conversational: single/multi-hop, temporal, adversarial |
| HaluMem | Hallucination at operation level |
| LongMemEval | Multi-session consistency |

### Metrics & targets
```
Token-level F1 ≥ 0.65            (SoTA ~0.72 full-context, ~0.61 selective)
Recall@k, Deducible Score, Hallucination Rate, Noise Reduction Rate
Tokens injected / turn ≤ 1,500   (85–90% reduction vs full-context)
```

### Latency targets (corrected Rev 2 — split by path)
- **Core retrieval** (DB ops only: vector + BM25 + graph + fusion + assembly): **p99 ≤ 200ms**
- **Augmented retrieval** (full mode, incl. adaptive gate + HyDE LLM calls, warm cache): **p99 ≤ 1.5s**
- **Lite mode end-to-end retrieval:** **p99 ≤ 250ms** (1 embedding call + core)

The rev 1 single 200ms target was unachievable with LLM calls in the path; splitting by path makes each target real and measurable.

---

## 24. Build Sequence

Walking skeleton first, then harness, then thicken. Each step is independently runnable.

### Step 0 — Walking skeleton (lite mode, vertical slice) ✅ DONE
Thinnest end-to-end loop, no breadth. Delivered:
- `docker-compose`: ArangoDB Enterprise 3.12.9.1 + Python core sidecar
- Core: minimal schema (`episodes`/`memories`/`entities` + BM25 view + idempotency indexes), `store` (WORM episode + episodic memory, idempotency-keyed), `retrieve` (tenant/agent-scoped BM25 + tiktoken token-budgeted assembly), FastAPI `/health` + `/v1/store` + `/v1/retrieve`
- Vercel adapter: `arangoMemory()` middleware — retrieve+inject in `transformParams`, best-effort store in `wrapGenerate`/`wrapStream`, memory-less degradation on failure
- Connection probe (`check.py`) and two targets (local + ArangoGraph, §6)
- **Verified** vs real Enterprise 3.12.9.1: store→BM25 retrieve round-trip, idempotency (3 docs from 4 calls), tenant isolation, ruff+mypy clean, adapter typechecks+builds
- *Deferred within Step 0:* LLM-only entity extraction (entities collection exists but is not yet populated); a live end-to-end `streamText` turn through the seam (both halves proven independently — needs an example app + model key → now owned by **Step 3.5**, the agentic simulation harness)
- *Resolved along the way:* uv src-layout editable-install flakiness and iCloud `.venv` corruption (§25)

### Step 1 — Test + eval harness ✅ DONE
testcontainers (Enterprise 3.12.9.1), fixtures, core HTTP contract tests, minimal LoCoMo runner, CI wiring (`make ci`). Delivered:
- **App-factory refactor:** `create_app(client=None)` + `get_client` dependency replaces the import-time client singleton, so tests point the app at an ephemeral container (random port). Module-level `app = create_app()` preserved for `make dev`/prod.
- **`conftest.py`:** session-scoped Enterprise 3.12.9.1 container (`--vector-index=true`, evaluation mode — no license); **fresh per-test database** (created/dropped each test) for isolation; tenant/agent fixtures; a `wait_for_searchable` helper for ArangoSearch eventual consistency.
- **Integration tests:** schema idempotency (+ unique-index assertion), store→retrieve round-trip, idempotency dedupe (4 calls → 1 record), distinct turns, tenant isolation.
- **HTTP contract tests:** `TestClient` over the factory app — health, store/retrieve shapes, empty-tenant, 422 validation — pinning the TS↔Python seam.
- **Eval harness:** minimal LoCoMo-style runner (`arango_memory.eval`) — ingest → query → score **Recall@k + token-F1** — with a smoke dataset and a CI regression gate. **Lite/BM25 only**; lite-vs-full comparison deferred to Step 2 (full mode doesn't exist yet).
- **CI:** `.github/workflows/ci.yml` runs `make ci` (lint + type + test) on a Docker-enabled runner.
- *Resolved along the way:* migrated `add_persistent_index` → `add_index({"type": "persistent", …})` (the former is deprecated).

### Step 2a — Core retrieval ✅ DONE
Vector + RRF + MMR + tiered token budget + mode threading. Delivered:
- **Pluggable embedder** (`embedding.py`): sync `Embedder` Protocol; deterministic `FakeEmbedder` (keyless — tests/sim) and `OpenAIEmbedder`. `get_embedder()` errors if `openai` is selected without a key (no silent degradation).
- **Write-time embeddings** on `memories` (`embedding`/`embedding_model`/`embedding_version`).
- **Lazy Faiss IVF index** (`ensure_vector_index`/`has_vector_index`): builds only once the corpus ≥ `n_lists` (ArangoDB ERR 1555 otherwise; guarded by a doc-count pre-check to avoid a phantom index). Cold start degrades to BM25 (§7); retrieval **self-heals** — the first read after warm-up builds the index.
- **Retrieval pipeline:** parallel BM25 + vector → **RRF fusion** → **MMR diversity** → **tiered token budget** (working/episodic/semantic/reasoning with roll-up). MMR applies in the BM25-only path too, since embeddings live on the docs.
- **Verified:** vector syntax (`add_index type:vector`, `APPROX_NEAR_COSINE`, ≥`nLists` training) probed empirically against 3.12.9.1; 21 tests green (5 embedding + 3 ranking + 2 vector + Step 1's 11).
- *Deferred:* graph expansion → Step 3 (needs entities/edges); HyDE/adaptive gate/caching → Step 2b.

### Step 2b — Full-mode enrichment ✅ DONE
HyDE, adaptive gate, query-hash caching — the lite/full switch is now meaningful. Delivered:
- **Pluggable generator** (`generation.py`): sync `Generator` Protocol; deterministic `FakeGenerator` (scriptable `handler`, keyless — tests/sim) and `AnthropicGenerator` (`claude-haiku-4-5`, system-block prompt caching). `get_generator()` errors if `anthropic` is selected without a key.
- **Adaptive gate** (`should_skip_retrieval`): a memory-less turn when the model is confident no stored context is needed (§9 stage 1).
- **HyDE** (§9 stage 2): embeds a hypothetical answer instead of the raw question; falls back to the raw query when generation is empty, so full mode degrades gracefully to the lite vector path.
- **`QueryCache`**: per-query caching of gate + HyDE results so repeats are free (§16). In-process for now; a durable cache is a later ops concern.
- **Verified:** 34 tests green (4 generation + 6 enrich + 3 full-mode integration + the prior 21). CI stays deterministic via the fake generator (no key).
- *Note:* a *meaningful* lite-vs-full quality comparison needs a real/scripted model → Step 3.5 sim harness.

### Step 3 — Thicken ingestion
Split into four sub-steps (decided rev 8).

#### Step 3a — Extraction → graph ✅ DONE
Pluggable extraction + entity/edge graph + write-time conflict detection. Delivered:
- **Pluggable extractor** (`extract.py`): sync `Extractor` Protocol; deterministic `FakeExtractor` (capitalized-span heuristic, keyless — tests/sim) and `SpacyExtractor` (behind the `extraction` extra). `get_extractor()` factory. **GLiNER/GLiREL + Haiku fallback deferred to 3d** (avoids torch in CI).
- **Schema:** `mentions`/`relates_to`/`produced_by` edge collections, the `memory_graph` named graph, and a unique entity natural-key index `(tenant_id, name, label)`.
- **Entity writes** (`entities.py`): AQL UPSERT (exact dedup + `mention_count`), entity embeddings, idempotent `mentions` (memory→entity) / `produced_by` (entity→episode) / `relates_to` (entity↔entity co-occurrence) edges.
- **Write-time conflict detection** (§8 Stage 3): cosine vs the tenant's entities → ≥ `entity_merge_threshold` (0.9) merge / ≥ `entity_flag_threshold` (0.6) create + flag `needs_review` for Dream State (§13) / else create. Brute-force per turn for now (entity vector index is a later optimization).
- **Idempotency:** extraction runs only on the first store of a turn, so replays don't double-count mentions.
- **Verified:** 45 tests green (4 extract + 7 entities + the prior 34); conflict thresholds tested deterministically via a stub embedder. CI stays torch-free.

#### Step 3b — Graph expansion ✅ DONE
1–2 hop traversal from seed entities over the populated graph, fused into retrieval (§9 stage 4). Delivered:
- **`_GRAPH_QUERY`:** seed memories → entities (`mentions`) → `relates_to` neighbours (0..`graph_hops`) → other memories mentioning them, ranked by minimum hop distance.
- **Fusion:** joins BM25 + vector as a third RRF signal (`source: graph`); seeds are the top hits from the lexical/vector lists. Tenant-scoped; a no-op when a turn produced no entities.
- **Config:** `graph_hops` (default 2, max 3).
- **Verified:** 48 tests green (3 graph-expansion + the prior 45) — connected-memory surfacing, tenant isolation, and the no-entity BM25 fallback.

#### Step 3c — Durable write path ✅ DONE
Async, durable writes so memory failures never block or break the agent turn (§15). Delivered:
- **Queue** (`queue.py`): `WriteIntent` (carries its idempotency `key`), `WriteQueue` Protocol, thread-safe `InProcessQueue` (the seam a Redis/SQS backend slots into later).
- **Worker** (`worker.py`): `WriteWorker` commits via `store()` with exponential backoff (`write_max_retries`, `write_backoff_base`); dead-letters to `failed_writes` on exhaustion; `replay_failed()` re-enqueues. `drain()` (sync, tests) + `start()/stop()` (daemon thread, own DB connection).
- **API**: `/v1/store` enqueues and returns `{status:"queued", episode_id, memory_ids}` (deterministic from the idempotency key; `entity_ids` resolved async). Worker starts/stops in the app lifespan.
- **Naming:** dead-letter collection is `failed_writes` (not `_failed_writes`; ArangoDB reserves `_*`).
- **Verified:** 55 tests (3 queue + 4 worker + the prior 48) — drain commits episode/memory/entities, retry-then-succeed, dead-letter on persistent failure, `replay_failed()`, and the async API round-trip.

#### Step 3d — Procedural + prospective indexing ✅ DONE
Procedural memory (ingestion + retrieval/reuse) and full-mode prospective indexing. Delivered:
- **Procedural** (`procedural.py`): `steps` collection + `TOUCHED` (step→memory) / `TRANSITION` (step→step) edges in `memory_graph` + a step natural-key index. `record_step` UPSERTs by `(tenant, agent, tool_name, outcome)` so a recurring pattern increments `use_count` (the reuse signal), writing `TOUCHED`/`TRANSITION`; `get_steps` lookup (tenant/agent-scoped, most-reused first).
- **Async path**: `StepIntent` joins `WriteIntent` on the durable queue; the worker dispatches by type (dead-letter/replay handle both).
- **Prospective** (`prospective.py`): full-mode `store()` generates hypothetical future questions (Step 2b generator) into `memories.prospective_queries`; the search view indexes the field and BM25 matches it — a memory becomes findable by a question it answers.
- **API**: `POST /v1/step`, `GET /v1/steps`.
- **Verified**: 62 tests (4 procedural + 2 prospective + 1 API step + the prior 55) — reuse/use_count, `TOUCHED`/`TRANSITION`, tenant scope, prospective findability, async step round-trip.

#### Step 3e — Heavy extraction tier ✅ DONE
GLiNER/GLiREL zero-shot NER + typed relation extraction and the Haiku fallback (§8 Stage 2), all behind the `Extractor` Protocol (extended additively with `extract_relations`). Delivered:
- **`GlinerExtractor`** (tier B): GLiNER NER + GLiREL typed relations; NER model + relation function injectable → tested with deterministic fakes (torch stays out of CI, behind the `extraction` extra).
- **`HaikuExtractor`** (tier C): entities + typed relations as JSON via a `Generator`; keyless in CI via `FakeGenerator`; one cached LLM call serves both `extract`/`extract_relations` (§16).
- **`LayeredExtractor`**: the A→B→C chain — spaCy → GLiNER fill → escalate to Haiku only when the cheap tiers yield `< extraction_escalate_below` entities.
- **Typed relations in the graph**: `write_entities` writes the typed `relates_to` label when present, else co-occurrence `associated_with` (typed written first; idempotent edge key).
- **Explicit `valid_time`** (`ingest/temporal.py`): deterministic keyless date parsing → `valid_time`/`valid_time_explicit` on entities + typed edges (closes the Step 4→3e deferral).
- New config (`extraction_provider` += `gliner|haiku|layered`, label/threshold knobs); `glirel` in the `extraction` extra (not `dev`); 10 keyless, torch-free tests.

### Step 3.5 — Agentic simulation harness (real-data validation)
Robust real-data validation of the end-to-end product — an agentic Vercel app using ArangoDB for both memory and actions (§22 "Agentic simulation harness"). Split into 3.5a (deterministic harness) and 3.5b (reference app).

#### Step 3.5a — Deterministic sim harness ✅ DONE
CI-friendly, keyless regression gate (`arango_memory/sim/`). Delivered:
- **Runner** (`runner.py`): `run_scenario` plays a multi-session agent loop with interleaved tool calls against the core's HTTP surface (`/v1/store`, `/v1/step`, `/v1/retrieve`) over a decoupled `HttpClient` Protocol (FastAPI `TestClient` satisfies it). Stubbed models → deterministic. Scoring reuses `eval` metrics.
- **Scenario** (`scenario.py` + `tests/data/sim_scenario.json`): multi-session conversation + tool calls + cross-session QA.
- **Gate** (`tests/test_sim.py`): asserts cross-session **recall** (lite + full), **procedural** memory + `use_count` reuse + `TOUCHED`/`TRANSITION`, graceful **write-failure degradation** (§15), and tenant **isolation**.
- **Placement deviation:** lives at `arango_memory/sim/` (mirroring `eval/`), not root `sim/` (§3), reusing the testcontainers fixtures; runs in the existing `make ci`.
- *Boundary:* a true lite-vs-full quality delta needs a real model → 3.5b.

#### Step 3.5b — Reference app + adapter tool-trace capture ✅ DONE
Adapter procedural capture + a runnable reference agent. Delivered:
- **Adapter** (`@arango-memory/vercel`): captures completed tool calls — pairs `tool-call` + `tool-result` parts from the prompt history (deduped by `toolCallId`, chained via `prev_step_key`) → `POST /v1/step`; outcome from the result output type (`error-*` → failure). Best-effort/non-blocking; one-turn lag is inherent to the LanguageModel middleware layer.
- **vitest** unit tests (mocked fetch + fake model): retrieve/inject, memory-less degradation, store, tool capture (success/failure, dedup, chaining).
- **CI**: new `adapter` job (typecheck + build + test) alongside `core`.
- **`examples/vercel-agent/`**: a minimal `generateText` loop wrapping `arangoMemory` with a `weather` tool — the manual/nightly end-to-end check (adapter → core → ArangoDB), typechecked against `ai@5` + `@ai-sdk/anthropic@2`. Closes the Step 0 deferred live-turn item.

#### Step 3.5c — Memory Dungeon (Next.js reference app) ✅ DONE (Standard scope)
The reference UI is **Memory Dungeon** (`examples/dungeon/`): a text-adventure where the world persists across sessions and the **NPCs lie** — catching a lie is the backend's bi-temporal supersession + conflict detection made playable; the map is the knowledge graph; tool calls are procedural memory. Built on Next.js (App Router) + the Vercel **AI SDK** (`streamText` + tools + `useChat` generative UI) + the shipped `arangoMemory()` middleware, in Vercel's Geist aesthetic (dark "candle-lit" + light "dashboard white", both in the locked mockup `docs/mockups/dungeon-ui.html`). **Standard scope** (3.5c-0 scaffold → 3.5c-3 lie engine) complete; host decided later (built against `CORE_URL` + docker-compose).
- **3.5c-0 — scaffold** ✅ App shell, locked dual theme, typed core client (`lib/core.ts`), `/api/health`, `docker-compose.yml` (ArangoDB + core via the existing `core/Dockerfile`); new **`dungeon` CI job** (typecheck + build).
- **3.5c-1 — playable loop** ✅ `/api/chat` runs `streamText` over a `gateway()` model **wrapped with `arangoMemory()`** (retrieve+inject, durable store, procedural-step capture); three tools — `look`/`move`/`take` (`ai` `tool()` + zod) — validate against a static seed world (`lib/world.ts`) and persist each fact to the core (best-effort, §15). The `useChat` client (`DungeonGame.tsx`) tracks `{roomId, inventory}` (localStorage, sent per request via `sendMessage` `body`), folds tool outputs back into state, and renders DM narration / player lines / tool notes. Keyless `vitest` world tests added to the `dungeon` CI job. Verified `next build` + `tsc` + tests clean.
- **3.5c-2 — generative UI + map** ✅ Tool outputs render as **generative-UI cards** (`components/cards.tsx`): a `RoomSceneCard` (per-room procedural art tint + description + exit/item chips) for `look`/`move`, and a pickup note for `take`, interleaved with the DM's serif narration. The **map pane is the live knowledge graph** (`DungeonMap.tsx` ← `/api/graph` ← `lib/graph.ts` `buildGraph`): entities are classified as **rooms** (matched to known room names → prominent, labelled, current-room highlighted) vs **lore** (faint strays the extractor caught), with `relates_to` edges fetched for room nodes (deduped) and a stable deterministic ring layout + CSS edge-draw/node-fade. Refetches after every turn. Keyless `vitest` graph tests (classification, edge dedup). Built on existing `/v1` endpoints — no core change. *(Refinement, rev 33: the room card's art panel is now a **memory glimpse** — a per-room subgraph (`roomMemory`) drawn as the room node + its remembered neighbours, an honest window into the graph instead of a decorative tint; the graph fetch is lifted to `DungeonGame` and shared by the map + cards.)*
- **3.5c-3 — the lie engine** ✅ NPCs with claims (`lib/world.ts`) — `talk(npc)` persists testimony + mints a claim entity per claim (via `seed`); `confront(npc, about)` resolves the claim, checks **exposability** (player holds the refuting item *or* heard the contradicting claim), and on a caught lie **supersedes the false fact** in the core (seeds the corrected fact, then the new endpoint). The **Dossier** (`Dossier.tsx`) computes, from world + game state, **trust meters** (drop sharply when caught lying) and a **Contradiction Ledger** (pending = exposable, caught = confronted → strikethrough + `superseded · valid_time invalidated`); the superseded lie entity then vanishes from the live map on the next refetch — bi-temporal supersession made visible. Two small **additive core endpoints**: **`POST /v1/supersede`** (`{new_key, old_key}`, write-only ABAC, wraps `lifecycle/conflict.supersede`) and `conflict_with` added to the entity projection (pytest-covered; **153 core tests**). Keyless `vitest` lie-engine tests (exposability, ledger, trust). This completes the **Standard scope**.

- **Graph Explorer tab** ✅ (rev 34) A dedicated `/graph` route (a **Play · Graph** tab) that visualizes the tenant's full **semantic graph** from ArangoDB with **React Flow** (`@xyflow/react`) + **elk** force layout. New additive core read **`GET /v1/graph`** (`graph_api.py`): entities (incl. **superseded**, carrying `invalid_at`) + `relates_to`/`Supersedes` edges, embeddings excluded (§17), pytest-covered (**156 core tests**). Themed `EntityNode` (Geist pill; superseded → struck/dim, `needs_review` → amber ring); pan/zoom + minimap; **click-to-inspect** drawer (label, `valid_time`, status, mentions), **edge-type filter** (per relationship), **supersession toggle** (show/hide `invalid_at` entities + `Supersedes` — the before/after-the-lie view), and **search/center**. Pure transforms (`lib/explorer.ts`) unit-tested keyless. The room-scoped mini-map stays in the play view.

*Deferred (Showcase follow-up):* the nightly "dungeon dreams" Cron → Dream State, generative scene art (Gateway→Blob), `@vercel/og` share cards, Edge Config knobs, and a **direct-provider fallback** (`@ai-sdk/anthropic` keyed by `ANTHROPIC_API_KEY`) so the app can be play-tested without a Vercel AI Gateway key.

The *full* benchmark run still completes at Step 7; this milestone establishes the harness and its regression gates.

### Step 4 — Lifecycle
Split into three sub-steps (decided rev 14).

#### Step 4a — Memory decay ✅ DONE
Episodic decay + spaced repetition (`lifecycle/decay.py`). Delivered:
- **Lazy decay**: `effective_strength = strength · exp(-λ · Δdays)` applied as a ranking multiplier in retrieval (recency/access boost, §9 stage 5) — always fresh, no batch.
- **Scheduled sweep**: `decay_sweep` soft-deprecates memories below `decay_floor` (`invalid_at`; never deletes). Callable now; scheduling is an ops concern (Step 7).
- **Spaced repetition**: surfaced memories get `accessed_at` reset + `access_count++` (Δt → 0).
- **Config**: `decay_lambda`, `decay_floor`. **Verified**: 70 tests (3 decay + prior 67) — recency ranking, access refresh, sweep soft-deprecation.
- *Deferred*: working-memory type + session TTL + SCM 7-item cap (separable feature).

#### Step 4b — Bi-temporal + Supersedes ✅ DONE
Conflict-resolution foundations (§5, §12), machinery-only. Delivered:
- **Bi-temporal fields**: entities + all edges carry `valid_time` (= ingestion_time), `valid_time_explicit` (false), `invalid_at` (null); edges also carry `weight` (1.0).
- **Supersedes**: new edge collection + `lifecycle/conflict.py:supersede(new_key, old_key)` — writes `Supersedes` (new→old) + soft-deprecates `old` (`invalid_at`), idempotent.
- **Conflict-aware traversal**: graph expansion filters `entity.invalid_at`/`related.invalid_at`, so a superseded entity no longer bridges the graph.
- **Verified**: 74 tests (4 + prior 70). *Deferred*: `needs_review` consumption → 4c (with confirmation); explicit temporal parsing → 3e (**done**, Rev 28); EWA `weight` → later.

#### Step 4c — Consolidation + Dream State ✅ DONE
Threshold-driven consolidation pass (§13) — completes Step 4. Delivered:
- **`lifecycle/dream.py:run_dream_state`**: reviews flagged (`needs_review`) + well-attested (`mention_count ≥ threshold`) entities; two-phase (decide → circuit-breaker → apply).
- **Conflict confirmation** (consumes `needs_review`): Haiku reviews the flagged entity vs its `conflict_with` target → `CONTRADICTS` ⇒ `supersede()` + clear; `DISTINCT` ⇒ clear.
- **Distillation**: well-attested entities get a one-sentence `summary` + `consolidated_at` (new entity fields).
- **Circuit breaker**: halts the whole run (applies nothing) if planned supersessions exceed `dream_breaker_threshold` (poisoning safeguard).
- **Verified**: 78 tests (4 + prior 74). *Deferred*: GAM session-topic trigger (separable); callable pass — scheduling → ops/Step 7; multi-tenant = caller iterates.

### Step 5 — Security
Split into two sub-steps (decided rev 17).

#### Step 5a — PII redaction + WORM ✅ DONE
Write-path security (§17). Delivered:
- **PII redaction** (`security/redact.py`): conservative regex for email/SSN/card/API-keys/bearer → typed placeholders, always on; a full-mode generator pass for contextual PII. Applied in `store()` **before** anything is hashed/persisted, so the original is never stored.
- **WORM** (`security/worm.py`): `worm_guard`/`WORM_COLLECTIONS`/`WormViolation` — client-layer enforcement primitive for insert-only `episodes`.
- **Config**: `redact_pii`. **Verified**: 84 tests (6 + prior 78).
- *Note*: embedding encryption-at-rest is a DB-deployment concern (would break vector search if app-level), not built here.

#### Step 5b — Right-to-be-forgotten + ABAC ✅ DONE
Access/deletion security (§17) — completes Step 5. Delivered:
- **`security/forget.py`**: `forget(tenant_id, agent_id?)` soft-deletes (sets `invalid_at` on the subject's memories + entities); `purge(tenant_id, agent_id?)` hard-deletes vertices + touching edges (episodes via the sanctioned WORM bypass) and drops the vector index (self-heal rebuilds). `drop_vector_index` added to the schema module.
- **API**: `POST /v1/forget` (soft-delete, write-only); `purge` stays an ops callable.
- **ABAC**: `store`/`step`/`forget` require `access_level == "write"` (else `403`); `retrieve` allows read. Adapter already ABAC-compliant (3.5b).
- **Verified**: 90 tests (6 + prior 84).

### Step 6 — Observability
Split into two sub-steps (decided rev 19).

#### Step 6a — Telemetry facade + core instrumentation ✅ DONE
OTEL spans (no-op default) + `MemoryMetrics` emitter (§18). Delivered:
- **`telemetry/`**: `MemoryMetrics` (`on`/`emit`/`clear` + singleton `metrics`) and `span(name, **attrs)` (otel-api, no-op without a configured provider).
- **Instrumented**: `retrieve` (`memory.retrieve` span + `retrieval` event: duration_ms/results_k/tokens_injected/mode; + try/except → empty + `degraded` event, completing core-side §15 read-degradation); `store` (`memory.write` span + `write` event); worker `write{dead_lettered}`.
- **Verified**: 95 tests (5 + prior 90), incl. an OTEL span asserted via an in-memory exporter.
- *Deferred*: OTEL meter instruments (span attrs + emitter carry values).

#### Step 6b — Lifecycle metrics ✅ DONE
Remaining §18 metrics via the 6a facade — completes Step 6. Delivered:
- **Counters**: `decay{pruned}` (sweep), `consolidation{promoted,superseded,cleared,breaker_tripped}` (Dream State), `conflict{detected}` (write-time detection).
- **Cache hit-rate**: `QueryCache` tracks hits/lookups + `hit_rate`, emits `cache{hit,hit_rate}`. (Dedicated *embedding* cache + its hit rate = future feature.)
- **Graph gauge + stats**: `stats(db, tenant_id)` per-tenant counts + `graph` gauge; `GET /v1/stats` (implements the §19 `stats` contract).
- **Verified**: 101 tests (6 + prior 95).

### Step 7 — Hardening + ops
Split into three sub-steps (decided rev 21); the final v1 step.

#### Step 7a — Migration runner ✅ DONE
Versioned schema migrations on top of the idempotent baseline (§6). Delivered:
- **`schema/migrations.py`**: `Migration(version, name, apply)` + `MIGRATIONS` registry + `run_migrations(db)` — `meta` collection tracks `schema_version`; pending migrations apply in order, exactly once.
- **Wiring**: `ensure_schema` runs migrations after the baseline. `MIGRATIONS` empty at v1; future changes register a `Migration`.
- **Verified**: 105 tests (4 + prior 101).

#### Step 7b — Ops CLI ✅ DONE
`python -m arango_memory.ops <cmd>` (`ops.py`) — admin/destructive, off the HTTP API. Delivered:
- `vector-rebuild` (`rebuild_vector_index`): drop + recreate the Faiss index.
- `embeddings-migrate` (`migrate_embeddings`): re-embed only stale docs (`embedding_version != current`) across memories + entities, then rebuild — idempotent.
- `replay` (`replay_dead_letters`): re-enqueue + drain `failed_writes`.
- Importable functions + thin argparse dispatch (env-driven connection). **Verified**: 109 tests (4 + prior 105).

#### Step 7c — Full benchmark runner ✅ DONE
LoCoMo benchmark runner/CLI (`eval/benchmark.py`) — completes Step 7 and the v1 sequence. Delivered:
- `run_benchmark` aggregates per-sample evals → overall Recall@k / mean token-F1 / mean tokens-injected + a per-category (**Deducible**) breakdown; `_evaluate_targets` compares to §23 (F1 ≥ 0.65, tokens/turn ≤ 1500, recall floor).
- CLI `python -m arango_memory.eval.benchmark <dataset> [--mode] [--k]` prints a report and exits nonzero below targets.
- Real LoCoMo data = manual/nightly BYO run; tested on the smoke slice. Hallucination/Noise-Reduction (answer + judge) out of scope.
- **Verified**: 113 tests (4 + prior 109).

---

> ✅ **v1 build sequence complete (Steps 0–7).** v2: **all §21 adapters shipped** — MCP server, LangChain/LangGraph, and CrewAI — plus **Step 3e** (heavy extraction tier) and **Step 3.5c** (Memory Dungeon reference app, Standard scope). Remaining: only the **Step 3.5c Showcase follow-up** (Cron "dungeon dreams", scene art, OG cards, direct-provider fallback) — pure demo polish.

### v2 (post-v1)
MCP server, LangChain/LangGraph adapter, CrewAI adapter + G-Memory tiers.

### Candidate enhancements (open ideas, not committed)

Drawn from a review of adjacent prior art (a self-evolving POI link-analysis
harness on the same ArangoDB substrate). Each is tagged to the phase where it
would land. None are commitments — they are recorded here so the spec stays the
single source of future direction.

1. **Lazy decay computed at query time** → **Step 4 (Lifecycle).**
   Evaluate `strength × exp(-λ × time_since_access)` as a ranking-time multiplier
   *inside* the AQL retrieval query rather than (only) via a scheduled batch job.
   Always-fresh, removes a moving part. Trade-off: a computed value can't be
   indexed, so it's a ranking multiplier only — the scheduled job is still needed
   for hard soft-deprecation (`invalid_at`). Candidate to become the *default*
   decay path (§11), with the batch job reserved for deprecation sweeps.

2. **Corroboration count + source reliability as first-class confidence inputs**
   → **Step 3 (ingestion conflict detection, §8 Stage 3) and Step 4 (conflict
   resolution, §12).** Today confidence is `1.0 observed / 0.6 seeded` plus EWA
   edge weights. Add (a) **corroboration count** — how many *independent*
   episodes assert the same fact (cheap; every episode is already a provenance
   anchor) — as a confidence boost and conflict-resolution tiebreaker, and
   (b) optional **source reliability** as a per-episode input to the score.

3. **Graph-algorithmic salience (centrality / community via Pregel)**
   → **Step 2 (retrieval ranking) and Step 4 (consolidation).** Retrieval is
   currently local (vector + BM25 + 1–2 hop traversal); we never compute global
   structure. Two uses: (a) **centrality** (e.g. PageRank) as a salience signal
   that boosts retrieval ranking and resists decay for hub entities; (b)
   **community detection** to cluster related entities before Dream State review,
   complementing the per-entity `mention_count` threshold (§13). Pregel ships with
   ArangoDB, so this is cheap to prototype.

4. **Ontology evolution — propose new relationship types, human-in-loop review**
   → **v2 research item (extends §13 Dream State), behind a config flag.**
   `relates_to` types are a fixed enum (§5). Let the consolidation layer detect a
   recurring cluster of `associated_with` edges that really represents a new,
   nameable relation, write a proposal record, and require human approval before a
   migration adds the new edge type. Dream State already reviews stored structure
   asynchronously, so this is an extension, not a new subsystem. **Tension:** it
   leans toward a curated application rather than a drop-in library — hence v2 and
   flag-gated, not a v1 commitment.

*Not adopted from that prior art:* LangGraph for the core (contradicts the
lite-mode zero-hot-path-LLM envelope, §10), its POI-specific domain schema, and
its React/Cytoscape UI — all out of scope for a domain-agnostic memory backend.

---

## 25. Development, Tooling & Infrastructure

Decisions about how the project is built, tested, and kept safe.

### Toolchain
- **Python:** `uv` (3.11+), `hatchling` build backend, `src/` layout. Lint `ruff`, types `mypy --strict`.
- **TypeScript:** `pnpm`/`npm`, `tsc` strict. Adapter targets `ai@5` + `@ai-sdk/provider@2`.
- **Containers:** Docker Compose for local ArangoDB + core sidecar.

### Dev venv relocation (resolved)
This repo lives under iCloud-synced `Documents`, which created `name 2.ext`
conflict copies — corrupting both source files and the virtualenv (335 dup files,
incl. a duplicate `tiktoken_ext/openai_public 2.py` that crashed tiktoken).
Decisions:
- The venv is relocated **outside** the synced tree to `$HOME/.venvs/arango-memory`.
- `core/Makefile` bakes in `UV_PROJECT_ENVIRONMENT` (relocated venv) + `PYTHONPATH=src`
  for every task (`sync`/`dev`/`check`/`test`/`lint`/`type`/`ci`/`clean-venv`), so
  commands work regardless of the (flaky) uv src-layout editable `.pth`.
- `.gitignore` ignores iCloud conflict copies (`* [0-9].*`).
- pytest uses `pythonpath=["src"]` so tests never depend on the editable install.
- Recommended (not enforced): move the repo out of iCloud, or keep using the Makefile.

### Editable-install note
uv's editable `.pth` for `src/` layout can intermittently drop off `sys.path`
across re-syncs. We do **not** rely on it for execution — `PYTHONPATH=src`
(Makefile, Dockerfile `ENV PYTHONPATH=/app/src`, pytest config) is the source of truth.

### Secret protection (three layers)
1. **`.gitignore`** — blocks env files, keys/certs, cloud credentials, token files; only `.env.example` is committed.
2. **gitleaks pre-commit hook** (`.pre-commit-config.yaml`) — scans staged content for secrets pasted *inside* files. One-time `pre-commit install` per clone.
3. **gitleaks GitHub Action** (`.github/workflows/gitleaks.yml`) — server-side scan on every push/PR, independent of local setup. Runs a pinned gitleaks binary via `gitleaks git .` (avoids the action's initial-commit diff-range bug).

Never commit `ARANGO_LICENSE_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `ARANGO_BEARER_TOKEN`.

### Git workflow
- Standalone repo: `github.com/mikefons/arango-agentic-memory` (independent of the
  `arango-demo-creator` repo whose checkout it nests inside).
- **Feature branch → PR → squash-merge**; never push directly to `main` (except the
  unavoidable initial bootstrap push). CI (gitleaks, later `make ci`) must be green before merge.

---

*End of Design Specification (rev 3)*
