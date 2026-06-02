# ArangoDB Agentic Memory System — Design Specification

> **Status:** Pre-implementation. Authoritative reference before any code is written.
> **Last updated:** 2026-06-02 (rev 2 — post-reassessment)
>
> **Rev 2 decisions:** Python-first core with a thin TypeScript client · v1 scope is Vercel-only · build a walking skeleton first, then a test/eval harness, then thicken each layer.

---

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
│   └── src/arango_memory/
│       ├── client.py      ← ArangoDB client + connection abstraction
│       ├── schema/        ← collection/view/index definitions, migrations
│       ├── ingest/        ← PII redaction, extraction, prospective indexing, writes
│       ├── retrieve/      ← HyDE, hybrid search, fusion, reranking, budget
│       ├── lifecycle/     ← decay, consolidation, Dream State worker
│       ├── api/           ← local/service HTTP API (FastAPI) — the boundary
│       └── telemetry/     ← OpenTelemetry instrumentation
├── packages/
│   └── vercel/            ← thin TypeScript client (LanguageModelV4Middleware)
│       └── package.json   ← pnpm
├── docker-compose.yml     ← ArangoDB + Python core sidecar for local dev
├── .env.example
└── README.md

# Deferred to v2 (not created in v1):
#   packages/mcp/          ← MCP server
#   adapters/langchain/    ← Python LangChain/LangGraph adapter
#   adapters/crewai/       ← Python CrewAI adapter
```

**Package managers:** `uv` for Python, `pnpm` for TypeScript.
**Publishing (v1):** `arango-memory` (PyPI, the core), `@arango-memory/vercel` (npm).

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

### ArangoSearch View

```json
{
  "name": "memory_search_view",
  "type": "arangosearch",
  "links": {
    "memories": { "fields": { "text": { "analyzers": ["text_en"] } } },
    "entities": { "fields": { "name": { "analyzers": ["text_en"] },
                              "summary": { "analyzers": ["text_en"] } } }
  },
  "primarySort": [{ "field": "ingestion_time", "direction": "desc" }],
  "commitIntervalMsec": 1000,
  "consolidationIntervalMsec": 10000
}
```

---

## 6. ArangoDB Infrastructure

### Target Version
**v3.12.9+** — required for vector-index auto-training (create index before data load).

### Deployment Targets
Connection-string abstraction; same code targets both:
- **ArangoDB Cloud (ArangoGraph)** — managed, production
- **Self-hosted / Docker** — local dev and on-prem (see `docker-compose.yml`)

### Startup Sequence
1. Connect, verify database
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
| Haiku extraction fallback | ❌ (spaCy/GLiNER2 only) | ✅ |
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
The schema supports this in v1; the CrewAI adapter that exercises it ships in v2 (§21).

---

## 15. Write Durability and Graceful Degradation

*(New in Rev 2 — closes the "fire-and-forget" correctness gap and the missing degradation story.)*

### Durable write path
Memory writes from the adapter are **asynchronous but durable**, not fire-and-forget:

- The adapter enqueues a write intent (idempotency-keyed) and returns immediately — the agent turn never blocks on memory.
- A core-side worker drains the queue and commits to ArangoDB with retry + exponential backoff.
- Persistent failures land in a **dead-letter** record (`_failed_writes`) for inspection/replay via `ops`.
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

### Middleware (`LanguageModelV4Middleware`, specificationVersion `'v3'`)
```
transformParams (BEFORE):
  → POST {coreUrl}/v1/retrieve  → inject assembled context into system prompt
  → on core/network failure: pass through with no memory (§15)

wrapGenerate / wrapStream (AFTER):
  → enqueue POST {coreUrl}/v1/store (durable, non-blocking)
  → capture tool traces → procedural memory
  → return immediately
```

The adapter holds **no memory logic** — all intelligence lives in the Python core. It is a transport + injection shim.

---

## 21. Deferred Adapters (v2)

Not built in v1. The schema and core API are designed to support them without refactor.

- **MCP server** (`packages/mcp`) — exposes core API as MCP tools (store/search/get/list/forget/seed/stats) for Claude Desktop, Cursor, Windsurf. Now a wrapper over the Python core's HTTP API.
- **LangChain / LangGraph** (`adapters/langchain`) — `BaseMemory` + `ArangoMemoryNode` for `StateGraph`. In-process Python (no HTTP hop needed).
- **CrewAI** (`adapters/crewai`) — shared crew memory store exercising the G-Memory 3-tier (§14). In-process Python.

---

## 22. Testing and Eval Harness

*(New in Rev 2 — sequenced immediately after the walking skeleton.)*

### Unit / integration
- **ArangoDB via testcontainers** — spin a real v3.12.9+ instance per test session; no mocking the database.
- Fixtures for tenants/agents/sessions; deterministic seed data.
- Contract tests for the core HTTP API (the TS↔Python seam) so the adapter and core can't drift.

### Eval harness (dev loop)
- Minimal **LoCoMo-style** runner: load multi-session conversations, ingest, query, score F1 / Recall@k / Deducible Score.
- Runs locally and in CI on a small fixed slice; full benchmark runs are manual/nightly.
- Lite vs full mode compared on the same slice to quantify the quality/cost trade-off.

### CI
- Lint + type (ruff/mypy for Python, eslint/tsc for TS)
- Unit + integration (testcontainers)
- Eval smoke (small slice, regression gate on F1)

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

### Step 0 — Walking skeleton (lite mode, vertical slice)
Thinnest end-to-end loop, no breadth:
- `docker-compose` with ArangoDB + Python core
- Core: minimal schema (episodes, memories, entities), `store` + `retrieve` (BM25 + naive vector + simple assembly), LLM-only extraction (defer spaCy/GLiNER2)
- Vercel adapter: `transformParams` retrieve-and-inject, `wrapGenerate` durable store
- **Done = one real `streamText` turn reads and writes memory across the TS↔Python seam**

### Step 1 — Test + eval harness
testcontainers, fixtures, core HTTP contract tests, minimal LoCoMo runner, CI wiring.

### Step 2 — Thicken retrieval
HyDE, RRF, MMR, graph expansion, tiered token budget, lite/full mode switch, caching.

### Step 3 — Thicken ingestion
Multi-stage extraction (spaCy → GLiNER2 → Haiku), prospective indexing, write-time conflict detection, idempotency keys, durable write path.

### Step 4 — Lifecycle
Decay (Ebbinghaus, TTL), Supersedes/bi-temporal, GAM consolidation trigger, Dream State worker + circuit breaker.

### Step 5 — Security
PII redaction, ABAC, cascade delete, embedding encryption, WORM enforcement.

### Step 6 — Observability
OTEL spans + metrics, MemoryMetrics emitter, degradation counters.

### Step 7 — Hardening + ops
Migration runner, `vector:rebuild`, `embeddings:migrate`, dead-letter replay, full benchmark run.

### v2 (post-v1)
MCP server, LangChain/LangGraph adapter, CrewAI adapter + G-Memory tiers.

---

*End of Design Specification (rev 2)*
