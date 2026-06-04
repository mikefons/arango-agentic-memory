# ArangoDB Agentic Memory System — Design Specification

> **Status:** Step 3b (graph expansion) implemented and verified. Authoritative reference.
> **Last updated:** 2026-06-04 (rev 9 — post Step 3b)
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

Not built in v1. The schema and core API are designed to support them without refactor.

- **MCP server** (`packages/mcp`) — exposes core API as MCP tools (store/search/get/list/forget/seed/stats) for Claude Desktop, Cursor, Windsurf. Now a wrapper over the Python core's HTTP API.
- **LangChain / LangGraph** (`adapters/langchain`) — `BaseMemory` + `ArangoMemoryNode` for `StateGraph`. In-process Python (no HTTP hop needed).
- **CrewAI** (`adapters/crewai`) — shared crew memory store exercising the G-Memory 3-tier (§14). In-process Python.

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

#### Step 3c — Durable write path ← NEXT
In-process queue + worker + retry/backoff + dead-letter (`_failed_writes`) + graceful degradation (§15).

#### Step 3d — Procedural + prospective indexing
`steps` collection + `TOUCHED`/`TRANSITION` edges (procedural/tool-trace ingestion); prospective indexing (full mode, uses the Step 2b generator); GLiNER/GLiREL + Haiku extraction fallback (§8 Stage 2).

### Step 3.5 — Agentic simulation harness (real-data validation)
Robust real-data validation of the end-to-end product — an agentic Vercel app using ArangoDB for both memory and actions (§22 "Agentic simulation harness"). Slotted here because it depends on full mode (Step 2), procedural/tool-trace ingestion, and the durable write path (Step 3). Two deliverables:
- **`sim/`** — deterministic, CI-friendly harness (stubbed/recorded model) scripting multi-session conversations + tool calls against the adapter+core; asserts recall on the real LoCoMo slice (lite **and** full), action/procedural-memory write+reuse, graceful degradation, and multi-tenant isolation.
- **`examples/vercel-agent/`** — reference Next.js app driving live `streamText` turns through adapter → core → ArangoDB (manual/nightly); doubles as the demo and closes the Step 0 deferred live-turn item.

The *full* benchmark run still completes at Step 7; this milestone establishes the harness and its regression gates.

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
