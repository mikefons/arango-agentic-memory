# ArangoDB Agentic Memory System — Design Specification

> **Status:** ✅ **v1 build sequence complete (Steps 0–7).** v2: all §21 adapters shipped (MCP, LangChain/LangGraph, CrewAI) + full §19 entity API + **Step 3e heavy extraction tier done**, hardened into a deployable service. Authoritative reference.
> **Last updated:** 2026-07-21 (rev 87 — SC-1a profiler proof: SC-1b flattened ingestion (store p50 plateaus ~1.4s); retrieve still grew (798→5080ms) ⇒ SC-1d caps per-entity memory fan-out to close it; §23)
>
> The **rev-by-rev build log** lives in [`HISTORY.md`](HISTORY.md); user-visible changes are in
> [`CHANGELOG.md`](../CHANGELOG.md); the doc map is [`docs/README.md`](README.md). This file is
> the current architecture + decisions.

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
│   ├── api.md             ← core API reference (✅ /v1 + in-process surface)
│   ├── ops.md             ← operations runbook (✅)
│   └── adapters/          ← per-adapter guides (✅ vercel/langchain/crewai/mcp)
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
  RRF merge (vector + BM25 + graph) → recency/access boost
  → [cross-encoder rerank, opt-in — RQ-2b] → MMR diversity. Hard cap k ≤ 10.
  │
  ▼
Stage 6: Context Assembly (token budget)   [CORE]
  Tiered (configurable maxMemoryTokens, default 1500):
    working 400 · episodic 700 · semantic 300 · reasoning 100
  Sort by score within tier; unused budget rolls up. tiktoken counting.
```

### Multi-hop mode (RQ-1)

`mode="multihop"` addresses the category-bound recall ceiling (DESIGN §23): a
multi-hop question needs evidence from turns that don't co-locate near one query
embedding, so no single top-k pass can gather the chain. It wraps the six stages:

```
Query Text
  │
  ▼
Decompose (one LLM call)   split into independent sub-lookups (≤ DECOMPOSE_MAX_SUBQUERIES)
  │                        0/1 lookups → [query], i.e. transparent single-shot fallback
  ▼
For [original query] +     run Stages 3–5 (arms → RRF → recency) → a fused candidate list
each sub-query:            per query. The original query is always included, so multihop
  │                        is a superset of single-shot (RQ-1d).
  ▼
Second-level RRF           fuse the lists at weight 1.0; a doc corroborated across
  │                        queries accumulates rank mass (the multi-hop signal)
  ▼
Stages 5–6 tail            MMR diversity → tiered token budget (unchanged)
```

Because the original query is one of the fused lists (and a ≤1-lookup decomposition
runs the *exact* single-shot path), the mode does not drop hits the full question
would have found on its own. Cost is N sub-queries = N
retrievals + 1 decompose call, so it is an *augmented* path (like HyDE), off the lite
hot path. It composes with neither the adaptive gate nor HyDE — decomposition is its
only LLM step.

### Cross-encoder rerank (RQ-2b)

The RQ-2a diagnostic showed the residual recall gap is a **ranking** failure, not a
first-stage one (100% of MuSiQue misses were in the fused pool, ranked below top-k, §23).
The `rerank=true` flag inserts a **cross-encoder** between fusion and MMR: it scores each
`(query, passage)` pair *jointly* — true relevance, not the lexical (BM25) / proximity
(vector) / hop-distance (graph) signals the arms fuse — over the top `rerank_top_n` fused
candidates, **replaces** each candidate's fused score with the rerank score, and reorders.
MMR + assembly then run on that order.

- **Composable, not a mode:** orthogonal to `lite`/`full`/`multihop`, so it stacks on any of
  them. Off by default; off the lite hot path (it loads/runs a model).
- **Superset-safe by scope:** only the top-N are reranked (candidates below the pool cutoff
  can't reach top-k anyway); replace-scoring means MMR selects by pure relevance.
- **Degrades** to the fused order if the reranker is unavailable or errors (§15) — memory
  never breaks the turn.
- **Provider:** a pluggable `Reranker` (keyless `FakeReranker` for tests; a local
  sentence-transformers cross-encoder, default `bge-reranker-base`, via the `rerank` extra).

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

Default: **lite**. Opt into full explicitly. A third mode, **multihop** (§9, RQ-1),
is orthogonal to this axis: it decomposes the query for multi-hop recall and does not
enable HyDE / the gate / write-time enrichments.

---

## 11. Memory Lifecycle

### Working Memory ✅ (rev 51)
- `type: "working"`, `expires_at` = `created_at + working_session_ttl_seconds`; a TTL
  index on `memories.expires_at` auto-deletes (episodic memories store `null` → ignored).
- Max `working_capacity` (default 7) active per (tenant, agent, session) — the SCM
  cap; overflow promotes the **oldest** working memory to `episodic` (clears its TTL).
- Written via `store(memory_type="working")` / `POST /v1/store {"memory_type":"working"}`;
  ephemeral scratch **mints no entities**. Retrieval already routes `working`-type hits
  into the working token-budget tier (§9).

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

- **EWA weights:** ✅ (rev 53) each corroboration updates the `relates_to` edge
  `weight` via a **recency-decayed EWA** in the `_RELATE` upsert —
  `weight = α·1 + (1−α)·OLD.weight·exp(−λ·Δdays(last_seen→now))` (seed = α). Stale
  relations decay toward 0; recently/frequently confirmed ones approach 1. The graph
  retrieval arm folds the bridging edges' mean weight into the bridge salience
  (`MAX(belief, centrality, weight)`), so the agent prioritizes recently-confirmed
  relations; surfaced in `/v1/graph` edges. Knobs: `weight_ewa_alpha`, `weight_lambda`.
- **Deterministic override:** human-edited config wins over LLM-extracted facts (checked first at retrieval).

---

## 13. Consolidation and Dream State

### Trigger: GAM Semantic Boundary ✅ (rev 52)
Consolidation does **not** run per turn. `store()` tracks a per-session running
`topic_embedding` (in the `sessions` collection, EWA-blended each turn). On each turn
with a `session_id`, it compares the turn vector to that running topic; if
cosine < `topic_shift_threshold` (0.7) it's a **topic shift**: the session's working
buffer is **flushed to episodic** (promote-relabel) and the session is flagged
`consolidation_due` (a `topic_shift` metric is emitted). The "consolidation check" is
**signal-only** — Dream State stays a separate scheduled/triggered pass, so the write
path never blocks on an LLM. First turn seeds the topic; idempotent replays are skipped.

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

**Cross-agent reads are first-class (MA-2).** `retrieve` accepts
`read_agent_ids: [...]` (on `AccessContext`) and filters every arm (BM25, vector,
graph) with `agent_id IN @agent_ids`, so one **fused** pass (RRF/MMR/decay/belief all
apply) spans an agent's private memory + shared crew tiers — no N-call stitching. Each
`MemoryHit` carries its writer's `agent_id` as **provenance**. Writes are unchanged:
`agent_id` stays the sole write identity, and every id is tenant-scoped by the AQL
(a cross-tenant id simply returns nothing). Per-agent read *restrictions* on a key are
a separate concern (MA-7).

`POST /v1/prime` (MA-3) is the handoff verb over this: given a task it composes one
budgeted briefing — retrieved history + the entities those memories mention + the read
agents' most-reused tool runs — so the next agent starts warm instead of hand-crafting
a query. See the [orchestration guide](orchestration.md) for the end-to-end pipeline
pattern (naming, per-harness recipes, the orchestrator/brain seam).

---

## 15. Write Durability and Graceful Degradation

*(New in Rev 2 — closes the "fire-and-forget" correctness gap and the missing degradation story.)*

### Durable write path
Memory writes from the adapter are **asynchronous but durable**, not fire-and-forget:

- The adapter enqueues a write intent (idempotency-keyed) and returns immediately — the agent turn never blocks on memory.
- A core-side worker **claims** an intent, commits to ArangoDB with retry + exponential backoff, then **acks** it (claim→ack, not a destructive pop, so a crash between claim and ack redelivers — rev 57).
- Persistent failures land in a **dead-letter** record (`failed_writes`; the leading underscore from earlier revs is dropped — ArangoDB reserves `_*` for system collections) for inspection/replay via `ops`.
- Because writes are idempotency-keyed, replays/redeliveries cannot duplicate (at-least-once is safe).
- **Queue backend** (rev 57, `WRITE_QUEUE_BACKEND`): `memory` (in-process, default — dev/CI; loses unacked work on crash) or `arango` (a durable `write_intents` collection — leased claims, survives restarts, multi-instance-safe via an exclusive-locked claim; **set in production**). Both sit behind the `WriteQueue` Protocol (`enqueue`/`claim`/`ack`/`nack`); **Redis/SQS** are drop-in adapters (roadmap).

For the walking skeleton, the "queue" may be in-process; production uses a durable queue (e.g., Redis/SQS) — the interface is identical.

### Consistency model (read-your-writes) — MA-1
Writes are **asynchronous by default**: `store` returns `"queued"` and a memory becomes
retrievable once the worker commits **and** the ArangoSearch view indexes it (a further
≤`commitIntervalMsec`, default 1 s). This is fine within one agent's turn, but at a
**multi-agent handoff** — agent A writes, agent B immediately reads — B can miss A's
final writes. Two opt-in barriers close that gap:

- **`store(..., sync=true)`** (and `step`): commit **inline** on the request thread
  (bypassing the queue) and force the search view to reflect it before responding
  (`status: "committed"`). Bypassing means a sync commit **does not dead-letter** — a
  failure returns `503` to the caller, who asked to block on the result. Idempotency
  keys make a sync commit and any concurrent async commit converge safely.
- **`POST /v1/flush {ctx, timeout_ms}`**: block until the tenant's queue has drained
  (no unacked intents) and the view is synced → `{"status":"flushed"}`, or
  `{"status":"timeout","pending":n}` (both HTTP 200 — a timeout is caller-branchable).
  "Drained" counts a dead-lettered write as done: flush means the queue emptied, not
  that every write succeeded.

Both force a view commit via arangosearch's `waitForSync` query option, so they cover
**BM25 + graph** (graph reads hit collections, already immediately consistent). The
**vector arm is not covered** — the Faiss index updates on its own cadence — so use
`sync`/`flush` at **stage boundaries, not every turn** (a forced view commit isn't free).

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
| Embedding calls (query + new memories) | 1–N (cached) | 1–N (cached) |
| Background (Dream State) LLM calls | amortized, not per-turn | amortized |

✅ **Dedicated embedding cache** (rev 54): a process-level LRU
(`embedding_cache.py`, `embed_cached()`) memoizes `embed(text)` so recurring inputs —
above all the **entity names** re-embedded on every mention, plus repeated queries and
idempotent replays — skip the provider. Keyed by `(tenant_id, model, version,
dimensions, sha256(text))` — **per-tenant namespacing** is the §24 timing-attack
defense. Distinct from the `QueryCache` (HyDE/gate LLM results); exposes `hit_rate` +
an `embedding_cache` metric (+ OTEL meter). Knobs: `embedding_cache` (on),
`embedding_cache_size` (10000).

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
Defenses against embedding-inversion: **per-tenant cache namespacing** and **embeddings
are never returned in API responses** (both enforced in code). **Encryption at rest is a
storage-layer concern, not application code** — field-level encryption would break vector
search, so rely on ArangoDB Enterprise storage encryption (or disk/volume encryption) at
deploy time; see [ops.md](ops.md). The application layer does not encrypt embedding fields.

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

OpenTelemetry spans + metrics, plus **structured logging** (rev 59): stdlib logging
on the `arango_memory` logger, `LOG_FORMAT=text|json` / `LOG_LEVEL`. A
`RequestLogMiddleware` assigns a correlation id (`X-Request-ID`, honoring an inbound
one and echoing it) and emits one access line per request; every record — including
the worker's dead-letter and a degraded retrieve — carries `request_id` + `tenant`
via contextvars. No built-in dashboard — users plug into their backend.

**Spans:** `memory.retrieve`, `memory.write` (no-op without a configured OTEL provider).

**Metrics (OTEL instruments actually emitted** — recorded centrally from `MemoryMetrics.emit`):
```
memory.writes                  counter    (outcome=ok|dead_lettered)
memory.write.duration          histogram  (ms)
memory.retrievals              counter    (mode tag)
memory.retrieval.duration      histogram  (ms; mode tag — the latency target metric)
memory.retrieval.results       histogram  (hits/turn)
memory.retrieval.tokens        histogram  (context tokens injected — key cost metric)
memory.degraded                counter    (op, reason — memory-less fallbacks)
memory.conflicts               counter    (entity conflicts detected)
memory.decay.pruned            counter
memory.consolidations          counter    (breaker_tripped tag) + memory.consolidation.changes
memory.cache.lookups           counter    (hit tag — HyDE/gate query cache)
memory.embedding_cache.lookups counter    (hit tag — derive hit-rate as a ratio)
```
Names normalize under the OTEL→Prometheus exporter (dots→`_`, `_total` for counters, units
appended) — see `deploy/observability/`. Dead-letter health = `memory.writes{outcome="dead_lettered"}`;
cache hit-rate is `rate(...lookups{hit="true"}) / rate(...lookups)` (no separate gauge).
Per-tenant graph counts are surfaced via `GET /v1/stats` + a `graph` emitter event rather
than as an OTEL gauge.

Programmatic: `MemoryMetrics.on("retrieval", handler)` event emitter.

---

## 19. The Core API (language-agnostic contract)

The Python core exposes a stable API consumed locally (in-process Python) and over HTTP (the boundary for the Vercel adapter and future v2 adapters). Keeping this contract neutral is what makes v2 adapters additive.

```
store(content, ctx)                  → ingestion pipeline, returns ids
retrieve(query, ctx, opts)           → retrieval pipeline, returns assembled context
prime(task, ctx, opts)               → task briefing: history + entities + tool runs (MA-3)
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
- **Concurrency / isolation** (`test_concurrency.py`, rev 63): multiple threads write/read at once (own connections) and tenant scoping is asserted to hold — no cross-tenant leakage through the shared search view; concurrent durable-queue workers process each intent exactly once (the exclusive `claim` lease, §15).
- Contract tests for the core HTTP API (the TS↔Python seam) so the adapter and core can't drift.
- Imports resolve via `pythonpath=["src"]` (pytest config), independent of the editable install (§25).

### Eval harness (dev loop)
- Minimal **LoCoMo-style** runner: load multi-session conversations, ingest, query, score F1 / Recall@k / Deducible Score.
- Runs locally and in CI on a small fixed slice; full benchmark runs are manual/nightly.
- Lite vs full mode compared on the same slice to quantify the quality/cost trade-off.
- **Step 1 status:** the runner is implemented (`arango_memory.eval`) but **lite/BM25-only** and scored on a tiny hand-built smoke slice — it validates the ingest→index→retrieve→score *plumbing*, not real-data quality. Real-data quality is the job of the simulation harness below.
- **Multi-agent handoff eval (MA-5, `eval/handoff.py`).** Scores the coordination layer, not just single-agent recall: a *writer* agent ingests facts + tool runs, then a *reader* (different `agent_id`) `prime`s across `read_agent_ids` (MA-2/MA-3) after a `force_view_sync` barrier (MA-1, zero sleeps) — measuring **context recall** (gold facts in the briefing) and **procedural recall** (gold tool runs surfaced). Scenarios cover clean A→B, a three-stage pipeline, distractor noise under a small budget, and a sync-boundary read. Keyless (BM25/FakeEmbedder) smoke slice runs as a pytest in CI, so reverting the cross-agent read or the barrier turns it red; `make handoff-eval` runs a larger BYO set. `sim/` (below) was folded into `eval/` — there is no separate `sim/` tree.

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

**Harnesses.** Retrieval-side deterministic metrics (Recall@k, token-F1,
tokens-injected, per-category) → `eval/benchmark.py` (gates a nightly run vs the
targets above). **Hallucination Rate + Noise-Reduction Rate** (rev 55) →
`eval/halu.py`: the full agent loop (retrieve → `generate_answer` → `judge_answer`)
with an **injectable generator + judge** (keyless `FakeGenerator` in CI, Haiku for a
real run). Hallucination Rate = answers with a claim unsupported by retrieved context;
Noise-Reduction Rate = answers that stayed focused on the relevant fact. Report-only
by default (§23 sets no numeric targets for these two); `--max-hallucination` /
`--min-nrr` optionally gate. CLI: `python -m arango_memory.eval.halu <dataset>`.

**Running the real LoCoMo benchmark (rev 70).** The public dataset is bring-your-own
(externally licensed, large — never committed; CI stays on the smoke slice).
`eval/locomo_convert.py` maps the official release into the runner schema, then the
benchmark gate runs as usual:

```bash
# 0. a running ArangoDB (docker compose up) + real providers for a real run:
#    EMBEDDING_PROVIDER=openai GENERATION_PROVIDER=anthropic + OPENAI/ANTHROPIC keys.
# 1. fetch locomo10.json from snap-research/locomo (per its license), then:
python -m arango_memory.eval.locomo_convert locomo10.json converted.json
make benchmark DATASET=converted.json MODE=lite      # then MODE=full; exits nonzero below §23
```

The converter orders `session_N` into the conversation, resolves each QA's first
`evidence` dia-id to the supporting turn text (the `gold_fact` Recall@k checks), and
maps the integer `category` to a name. **Adversarial (category 5) and evidence-less
questions are excluded** from the scored set (no fact to retrieve) and counted in the
conversion stats — so headline Recall@k/F1 stay comparable to the targets. The report
also prints **retrieval p50/p95/p99 latency** (rev 78, from the in-run recorder) against
the §23 targets — informational, since wall-clock is environment-dependent; the quality
metrics remain the pass/fail gate. **Run the full set** (not one sample) so the shared
vector index trains (≥ `VECTOR_N_LISTS` docs) and the vector arm engages. This is a
*retrieval-quality* run; answer-generation quality (Hallucination/NRR) is the separate
`halu.py` harness.

### P1 real-data results (rev 79)

First real LoCoMo run (10 conversations, 1,531 scored questions, `openai` embeddings).
The run surfaced — and we fixed — a chain of retrieval-ranking defects; recall roughly
doubled and the F1 metric was corrected from a construction artifact into a real
end-to-end number.

| Stage | Recall@k | token-F1 | What changed |
|---|---|---|---|
| First real run | 0.215 | — | baseline (post MA-8 index/logging fixes) |
| MMR relevance = fused score (#125) | — | — | MMR ranked by query-cosine, discarding fusion → fixed |
| Graph arm down-weighted in RRF (#126) | 0.31 | — | equal RRF weight let the query-agnostic graph arm bury BM25 (0.06→0.48 in isolation) |
| `MMR_LAMBDA=1.0` default (#127) | 0.44 | — | diversity was gating *what retrieval finds*; relevance-first |
| F1 scores a generated answer (#131) | 0.41 | **0.21** | F1 had compared a raw retrieved turn to a short gold answer — unreachable by construction; now generates then scores |

**Headline (lite + real generator): Recall@k ≈ 0.42, token-F1 ≈ 0.21.**

**Findings.**
- **BM25 is the dominant relevance signal** for this corpus. The vector and graph arms
  rank by *proximity* / *hop-distance*, not "does this answer the query"; at equal RRF
  weight each **displaces** correct BM25 hits (worse than useless), hence `RRF_GRAPH_WEIGHT`
  (0.1) and `RRF_VECTOR_WEIGHT` (1.0, tunable). Configured via per-arm RRF weights (#130).
- **HyDE (full mode) did not help here** — it rehabilitated the vector arm at high
  influence (0.14→0.36) but still lowered recall vs BM25-led lite (0.44→0.36), because the
  question↔evidence lexical overlap (~0.27) is a *retrieval-content* gap, not a query-form
  one. `ADAPTIVE_GATE` (#129) is toggle-able so full-mode gains aren't masked by the gate.
- **F1 is recall-bounded** (≈ 0.5 × recall): you cannot answer what was never retrieved,
  so the lever for both is recall.

**The plateau is category-bound.** Single-hop ≈ 0.36 and temporal ≈ 0.33 are reachable by
single-shot retrieval; the multi-hop category lagged in the full run, which motivated the
RQ-1 multi-hop-decomposition experiment below. That experiment came back **negative** —
see the next subsection — so the residual gap to 0.6 is a *retrieval-content* gap
(question↔evidence lexical overlap ≈ 0.27), not one that query decomposition can close.

### RQ-1 multi-hop decomposition — benchmark-dependent (rev 82)

RQ-1 hypothesized that decomposing a multi-hop question into sub-lookups would lift
multi-hop recall (DESIGN §9, `mode="multihop"`). The result depends entirely on whether the
benchmark's evidence is *actually* multi-turn: **negative on LoCoMo, strongly positive on
MuSiQue.**

**LoCoMo — negative** (281-question multi-hop subset, real `openai` embeddings, `anthropic`
generator):

| Config | Recall@k | token-F1 |
|---|---|---|
| **lite (single-shot)** | **0.317** | 0.257 |
| multihop (decomposition + original-query superset fix) | 0.132 | 0.164 |

Decomposition *hurt* recall by more than half. Root cause is the data: every LoCoMo
multi-hop `gold_fact` is a **single evidence turn** (median 168 chars). These questions are
multi-hop in *reasoning* (e.g. "home country" → *Sweden*), not in *retrieval* — there is one
turn to find, the **full question** matches it best, and splitting the query floods the pool
so second-level RRF outvotes the one gold turn (the superset fix keeps it in the *pool* but
not the top-k). With a single-turn target, decomposition has no headroom to beat single-shot.
(This also corrected the stale "multi-hop ≈ 0.19 anchor": on the clean subset lite multi-hop
is **0.317**, not 0.19.)

**MuSiQue — positive** (200-question smoke of MuSiQue-Ans dev via BX-1's converter +
multi-evidence metric; same providers). MuSiQue is non-shortcuttable and genuinely
multi-evidence (2–4 supporting paragraphs per question):

| Config | all-hops Recall@k | recall-frac | token-F1 |
|---|---|---|---|
| **lite (single-shot)** | 0.430 | 0.682 | 0.307 |
| **multihop** | **0.595** | **0.777** | **0.376** |
| Δ | **+0.165 (+38%)** | +0.095 (+14%) | +0.069 (+22%) |

Decomposition lifts every metric, and the **strictest one moves most**: all-hops Recall@k
(+0.165, ≈4–5 s.e. at n=200) is literally "did we gather the *complete* chain" — direct
evidence the mechanism works as designed. `recall-frac` recovered ~30% of the missing-support
gap (0.318 → 0.223). F1 tracked recall (recall-bounded). Cost is the expected trade:
multihop p50 ≈ 4.2s vs lite ≈ 1.2s (~3.4×), off the hot path, opt-in.

**Conclusion.** `mode="multihop"` is a **correct, opt-in** retrieval mode that **helps when
recall requires assembling ≥2 evidence turns** (MuSiQue) and is neutral-to-harmful when the
target is a single turn (LoCoMo) — use it accordingly. The original-query superset fix
(#137) is what makes it safe: multihop = single-shot's hits **plus** the extra hops. The
lever for LoCoMo's 0.42 → 0.6 remains retrieval *content* (ROADMAP RQ-2), now measurable on
MuSiQue via BX-1's `recall-frac`. See [ops.md](ops.md) for run steps.

### RQ-2 retrieval-content gap: diagnostic + reranker (rev 83)

RQ-2a's miss diagnostic (`eval.pool_diag`) on the MuSiQue 200-Q set found **100% of recall
misses are *ranking* failures** — the gold evidence is in the fused pool but ranked below
top-k (97/97 misses in-pool, 0 absent). So the lever is a reranker, not query expansion.
*(Caveat: MuSiQue's per-question ~20-paragraph tenants make `pool@100` ⊇ the whole corpus,
so this proves the failure is ranking in the given-context setting; it does not test
first-stage recall on a large open corpus.)*

RQ-2b's cross-encoder reranker (`rerank=true`, local `bge-reranker-base`) delivered the
predicted lift:

| Config | all-hops Recall@k | recall-frac | token-F1 |
|---|---|---|---|
| lite (baseline) | 0.430 | 0.682 | 0.307 |
| rerank | 0.565 | 0.757 | 0.353 |
| multihop | 0.595 | 0.777 | 0.376 |
| **multihop + rerank** | **0.810** | **0.905** | **0.443** |

Each lever helps alone (rerank +0.135, multihop +0.165 all-hops), but **stacking them is
super-additive:** the two gains sum to +0.300, yet the combined config lifts **+0.380** over
baseline (0.430 → 0.810 all-hops; recall-frac 0.682 → 0.905 — ~90% of each evidence chain
retrieved). The synergy is mechanistic: **multihop *fills* the pool** (decomposition
retrieves the extra-hop evidence a single query misses) and **rerank *orders* the pool** (the
cross-encoder promotes the most relevant candidates into top-k). Alone, rerank can only
reorder the limited gold single-shot fused, and multihop's RRF doesn't rank its gathered gold
optimally; together they are complementary stages of one funnel — multihop maximizes
gold-in-pool, rerank cashes it into top-k.

Both are opt-in and off the lite hot path; the reranker does not reach the ~1.0 ceiling
(`bge-reranker-base` is small). The stacked config is the heaviest (decompose + N sub-query
retrievals + cross-encoder, p50 ~9s/question) — the **recommended max-recall setting**
(`MODE=multihop RERANK=--rerank`), traded against latency. See [ops.md](ops.md) for run steps.

### Open-corpus scalability finding (BX-2 pooled run)

All results above are on **given-context** MuSiQue (per-question ~20-paragraph tenants). The
first attempt to test *open-corpus* first-stage recall — BX-2's `--pooled` converter, which
puts all questions' paragraphs in **one tenant** — surfaced a **scalability wall** instead of
a recall number: a 3,075-paragraph pooled corpus took **~12 h to ingest** and then **timed out
on retrieval** (`ReadTimeout` at 60 s). Two limits the 20-doc tenants had masked:

1. **Ingestion ~O(n²):** every `store()` resolves extracted entities against *all existing*
   entities in the tenant (merge/dedup), which grows with corpus size — so per-write cost
   climbs as the tenant fills.
2. **Graph-arm retrieval fan-out:** the `relates_to` traversal over a dense single-tenant
   entity graph blows the 60 s query timeout (the same fan-out tamed at 200 docs, unbounded
   at 3,000).

Neither is a *retrieval-quality* result — they are engineering limits of a single large-corpus
tenant. **BX-3** (ROADMAP) gets the first-stage-recall number anyway by routing around both
(ingest with `extract=False`, probe with `graph_hops=0` — first-stage recall needs neither
entities nor the graph arm). Both underlying limits are now fixed (**SC-1**, ROADMAP): the
**O(N²) ingestion** by **SC-1b** — ANN entity resolution (a Faiss IVF index on
`entities.embedding` → top-k nearest instead of the full tenant scan, §7); the **graph-arm
retrieval fan-out** by **SC-1c** — `GRAPH_MAX_NEIGHBORS` caps the `relates_to` expansion
before the memory join — and **SC-1d** — `GRAPH_MAX_MEMORIES_PER_ENTITY` caps each related
entity's `INBOUND mentions` join, so a dense single-tenant graph can't explode the traversal.

The **SC-1a profiler** (`eval/scaling_profile`, one tenant, 500 → 3,000 entity-rich memories)
gives the before/after empirical proof:

| tenant size | store p50 (ms) | retrieve p50 (ms) |
|---|---|---|
| 500 | 909 | 798 |
| 1,500 | 1,370 | 2,209 |
| 3,000 | 1,421 | 5,080 |

**Ingestion is bounded (SC-1b confirmed):** store p50 climbs through the cold-start scan then
**plateaus at ~1.4 s from 1,500 → 3,000** (once the entity ANN index trains) — not the O(N²)
climb the pre-fix run showed (~12 h for 3,075). **Retrieval, at the time of that run, still
grew** (798 → 5,080 ms): SC-1c bounded the *neighbour* breadth (no more 60 s timeout, p99 ≤
9.6 s) but not the *per-entity* memory fan-out — a hub entity's mentions grow as the tenant
fills, so `INBOUND mentions` kept scaling. **SC-1d** closes that residual by capping the
memories expanded per entity (total graph work ⇒ `MAX_NEIGHBORS × MAX_MEMORIES_PER_ENTITY`,
tenant-size-independent). Note the profiler's synthetic corpus is *pathologically* dense (50
hub entities shared across 3,000 memories); real corpora (entities mentioned by a handful of
memories) sit far below the default caps and are untouched by either bound.

### Open-corpus first-stage recall — real gap (BX-3 result)

The BX-3 lightweight probe (`pool_diag --lightweight`, `extract=False` + `graph_hops=0`) ran
the pooled 3,075-paragraph corpus in **minutes, no timeouts**, and answered the question the
given-context runs structurally could not:

| Regime | hit@10 | of misses: ranking (in-pool) | of misses: recall (out-of-pool) |
|---|---|---|---|
| given-context (RQ-2a, ~20-doc tenants) | 76% | 100% | **0%** |
| **open corpus** (BX-3, 3,075-doc tenant, pool@100) | 53% | 67% | **33%** |

**First-stage recall *is* a real gap on an open corpus.** With ~3,000 distractors instead of
~20, BM25 + vector fail to surface the gold into the top-100 pool for **33% of misses** —
invisible in the given-context setup (where the pool *was* the whole corpus, so RQ-2a's "0%
out-of-pool" was an artifact). (hit@10 also fell 76% → 53%: open retrieval is simply harder.)

**Widening the pool characterizes that 33% — mostly a knob, small residual project.** Re-running
at `--pool 500` reclassifies ~half of the recall-misses as in-pool:

| pool | ranking (in-pool) | recall (out-of-pool) |
|---|---|---|
| 100 | 67% | 33% |
| **500** | **82%** | **18%** |

So ~15% of misses were **tail-reachable** (gold at ranks 100–500, just below the cutoff → a
bigger candidate pool + rerank recovers them, cheap), but **~18% of misses (~8.5% of all
support) stay out-of-pool even at 500** — a genuine, irreducible first-stage gap no pool size
or reranker can touch. Takeaways: (1) for open corpora, **widen `candidate_pool` and rerank** —
that captures the bulk; (2) the reranker remains the dominant lever (82% of misses in-pool at
pool@500); (3) a **modest residual first-stage gap (~8.5% of support)** genuinely needs better
first-stage retrieval (query expansion / stronger embeddings / `prospective_queries`) — real
but small ROI, recorded as a future investigation, not scheduled.

### Latency targets (corrected Rev 2 — split by path)
- **Core retrieval** (DB ops only: vector + BM25 + graph + fusion + assembly): **p99 ≤ 200ms**
- **Augmented retrieval** (full mode, incl. adaptive gate + HyDE LLM calls, warm cache): **p99 ≤ 1.5s**
- **Lite mode end-to-end retrieval:** **p99 ≤ 250ms** (1 embedding call + core)

The rev 1 single 200ms target was unachievable with LLM calls in the path; splitting by path makes each target real and measurable.

---

## 24. Build Sequence

Walking skeleton first, then harness, then thicken. Each step is independently runnable.

> **Next phase:** the multi-agent handoff work packages (read-your-writes, cross-agent
> retrieval, `/v1/prime`, output capture, handoff eval, per-agent authz) are scoped in
> [ROADMAP.md](ROADMAP.md) — the coordination layer that turns the single-agent memory
> into the shared "agent brain" (§14) for orchestration pipelines.

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
- **Write-time conflict detection** (§8 Stage 3): cosine vs the tenant's entities → ≥ `entity_merge_threshold` (0.9) merge / ≥ `entity_flag_threshold` (0.6) create + flag `needs_review` for Dream State (§13) / else create. Candidate generation is by **ANN** once the tenant warms: a Faiss IVF index on `entities.embedding` (SC-1b) returns the top-k nearest existing entities per write instead of full-scanning the tenant's entities — the fix for the O(N²) ingestion the pooled-corpus run exposed (§23). Below the index's training threshold it falls back to the full scan (fine at small N). The merge/flag decision is unchanged; ANN candidate generation is *approximate* (top-k recall), a deliberate accuracy-for-scale trade, and degrades to the scan on any ANN error.
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

- **Direct-provider fallback** ✅ (rev 35) `lib/model.ts` `resolveModel()` — prefers the AI Gateway, but when `AI_GATEWAY_API_KEY` is unset and `ANTHROPIC_API_KEY` is present it calls Anthropic directly (`@ai-sdk/anthropic`), so the dungeon is play-testable without a Gateway account. Pure `chooseProvider()` selection unit-tested keyless.

- **Dungeon dreams (Dream State)** ✅ (rev 36) New additive core read **`POST /v1/dream`** (write-only ABAC) wrapping `lifecycle/dream.run_dream_state` — reviews flagged/well-attested entities, confirms conflicts → supersede, distills summaries, with the circuit breaker; returns a `{reviewed, superseded, consolidated, cleared, breaker_tripped}` report. pytest-covered (**158 core tests**). A **✦ dream** button in the play header ("the keep dreams") POSTs `/api/dream`, shows the report as a transient toast, and refreshes the graph; a **Vercel Cron** route (`/api/cron/dream` + `vercel.json`, daily) runs it automatically on deploys. With a real background model on the core (`ANTHROPIC_API_KEY`) the Haiku conflict-confirm + distillation are exercised; with the keyless default it still reviews + clears.

- **OG share cards** ✅ (rev 37) `app/api/og/route.tsx` renders a shareable "Dungeon Run" image (1200×630) via **`next/og`** `ImageResponse` (built into Next, no dep) — entities/relations counted live from the core (`GET /v1/graph`) + items/lies/room from the client run. A **⧉ share** button (play header) opens it with the current stats (`lib/share.ts` `buildShareUrl`, unit-tested). Renders locally; no external service.

- **Feature toggles (Edge Config)** ✅ (rev 38) `lib/flags.ts` — all features **off by default**; opt in via env (`SCENE_ART`, `DUNGEON_HINT`) or override at runtime via **Vercel Edge Config** (a `dungeon` key, read only when `EDGE_CONFIG` is set; `@vercel/edge-config` dynamically imported). `GET /api/flags` exposes them to the client. First knob: **`hint`** — when on, the chat route appends a hint instruction to the DM system prompt (off → prompt unchanged). Pure `flagsFromEnv` unit-tested.

- **Scene art** ✅ (rev 39) `GET /api/scene?room=` — gated behind the `sceneArt` flag: generates a dark-fantasy room image (`experimental_generateImage`, OpenAI image model) and caches it in **Vercel Blob** (keyed by room slug, generated once), returning the URL. Heavy deps (`ai`/`@ai-sdk/openai`/`@vercel/blob`) are **dynamically imported** so they stay inert when off; returns `204` (cards keep the memory glimpse) unless the flag + `OPENAI_API_KEY` + `BLOB_READ_WRITE_TOKEN` are all set. `RoomSceneCard` uses the image as the card backdrop (with a scrim) under the memory-glimpse constellation when enabled. Pure `roomSlug`/`scenePrompt` unit-tested.

**Step 3.5c Showcase follow-up is complete** — every deferred item shipped as a config-gated toggle (Cron dreams, OG cards, feature flags/Edge Config, scene art), all off by default.

The *full* benchmark run still completes at Step 7; this milestone establishes the harness and its regression gates.

### Step 4 — Lifecycle
Split into three sub-steps (decided rev 14).

#### Step 4a — Memory decay ✅ DONE
Episodic decay + spaced repetition (`lifecycle/decay.py`). Delivered:
- **Lazy decay**: `effective_strength = strength · exp(-λ · Δdays)` applied as a ranking multiplier in retrieval (recency/access boost, §9 stage 5) — always fresh, no batch. Folded into the **BM25 + graph AQL arm SORTs** (rev 48) so freshness shapes candidate *selection* (pool membership), plus a uniform post-fusion pass for final magnitude; the vector arm stays index-pure (`APPROX_NEAR_COSINE` can't combine without losing acceleration), reached via the post-fusion pass.
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
- *Follow-on shipped (rev 45)*: OTEL **meter instruments** — counters/histograms (`memory.*`) recorded centrally from `metrics.emit(...)`, so every emit site feeds any otel backend with no call-site changes (no-op without a `MeterProvider`).

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

> ✅ **v1 build sequence complete (Steps 0–7).** v2: **all §21 adapters shipped** — MCP server, LangChain/LangGraph, and CrewAI — plus **Step 3e** (heavy extraction tier) and **Step 3.5c** (Memory Dungeon reference app, Standard scope). The **Step 3.5c Showcase follow-up is also complete** (dreams Cron, OG cards, Edge Config flags, scene art — all config-gated). The project is feature-complete against the spec.

### Roadmap & backlog

> The v1 build sequence, v2 (§21 adapters + G-Memory tiers, entity API, Step 3e),
> and Step 3.5c (Memory Dungeon + Showcase) are all **shipped** — the project is
> feature-complete against the spec. Everything below is enhancement/hardening
> work, **not gaps**. This is the single, prioritized source of future direction.

**Shipped from this list:** ✅ **Corroboration count + source reliability → belief**
(rev 41). `confidence` stays the source prior (observed 1.0 / seed 0.6); a new
**`belief`** = `confidence × (1 − (1−base)^reliability_sum)` blends corroboration
(each independent episode adds its `source_reliability` to `reliability_sum`) with
source trust. Entities accumulate it on every corroborating write; **`relates_to`
edges gained a corroboration counter** (UPSERT-increment, once per pair per
episode); Dream State uses belief as the **conflict-resolution tiebreaker** (the
better-attested entity survives); belief boosts the **graph retrieval signal**;
and `belief`/`corroboration` are surfaced in `/v1/entity`, `/v1/entities`, and
`/v1/graph`. `store(source_reliability=…)` + `POST /v1/store {source_reliability}`
thread the per-source trust. (§8/§12)

✅ **Graph-algorithmic salience — PageRank centrality** (rev 42). **Note:** ArangoDB
**removed Pregel in 3.12**, so centrality is computed **in-process** — a short
PageRank power-iteration (`lifecycle/salience.py`) over the tenant's small entity
subgraph (no Pregel, no license, keyless-testable). `POST /v1/salience` recomputes
+ persists normalized `centrality` (0..1, hub = 1.0); it boosts the **graph
retrieval signal** (max of belief/centrality) and is surfaced in `/v1/entity`,
`/v1/entities`, `/v1/graph`. The dungeon recomputes it on "✦ dream" and sizes
Graph-Explorer node dots by it.

✅ **Core API reference** (rev 43) — [`docs/api.md`](api.md) documents the full
`/v1` HTTP contract (every endpoint, request/response, ABAC, conventions) + the
in-process Python surface + the pluggable providers.

✅ **Operations runbook + per-adapter guides** (rev 44) — [`docs/ops.md`](ops.md)
(run targets, env config, durable write/dead-letter `replay`, scheduled jobs,
vector index, observability, security/forget, schema) + [`docs/adapters/`](adapters/)
(index + one guide each for Vercel AI SDK, LangChain/LangGraph, CrewAI, and the MCP
server).

✅ **OTEL meter instruments** (rev 45) — counters + histograms (`memory.*`:
writes/retrievals + their latency/result/token histograms, degraded, conflicts,
decay, consolidation, cache) recorded centrally inside `metrics.emit(...)`, so all
existing emit sites feed any OpenTelemetry backend (Prometheus/Datadog/Grafana)
with no call-site changes. No-op without a configured `MeterProvider` (keyless CI).

✅ **Graph community detection** (rev 46) — deterministic **label propagation**
(`lifecycle/community.py`) over the tenant's `relates_to` subgraph (in-process,
keyless, like centrality; no Pregel). `POST /v1/community` recomputes + persists a
dense integer `community` label per entity (surfaced in `/v1/entity`,
`/v1/entities`, `/v1/graph`). It **scopes Dream State review**: the conflict-confirm
+ supersede is skipped for a flagged pair in **different** communities (structurally
distant → unlikely the same real-world entity), with a no-op fallback when either is
unlabeled — so prior behavior is preserved until a community pass runs.

✅ **Dungeon community coloring** (rev 47) — the Graph Explorer hues each entity
dot by its `community` label (deterministic golden-angle HSL via `communityColor`),
a sibling to the centrality node-sizing cue; superseded/review keep their status
colors. The ✦ dream flow (and the nightly cron) recompute communities **before**
dreaming so the Dream State scoping gate engages. Always-on, like the centrality cue.

✅ **Ontology evolution** (rev 49, flag-gated v2 research) — `lifecycle/ontology.py`
groups `associated_with` co-occurrence edges by endpoint **label-pair**, and (when
`ontology_evolution` is on) asks the generator to name the relationship a recurring
cluster (≥ `ontology_min_support`) represents, recording a **proposal** in the new
`ontology_proposals` collection — it never mutates the graph on its own. Human-in-loop
API: `POST /v1/ontology/scan` (propose), `GET /v1/ontology/proposals` (review),
`POST /v1/ontology/approve` (relabel the tenant's matching `associated_with` edges to
the proposed type — a scoped data migration; edge "types" are attribute values, not
collections) / `…/reject`. Off + 404 by default; keyless (the Fake generator proposes
nothing).

✅ **Dungeon ontology-review UI** (rev 50) — a **Play · Graph · Ontology** tab
(`/ontology`) surfaces `ontology_proposals` for one-click **approve/reject** (the
human-in-loop step), with a "✦ scan bonds" button to trigger a proposal pass. Reads
through `/api/ontology` → the core's flag-gated endpoints; when `ONTOLOGY_EVOLUTION`
is off the core 404s and the tab shows a disabled note. Closes the ontology feature
end-to-end.

✅ **Working-memory tier** (rev 51) — a session-scoped `working` memory type
(`store(memory_type="working")` / `POST /v1/store`): `expires_at` + a TTL index on
`memories.expires_at` auto-expire it (episodic stores `null` → ignored), the SCM cap
(`working_capacity`, default 7) promotes the oldest overflow back to `episodic`, and
working scratch mints no entities. Retrieval's existing working tier routes the hits.
`memory_type` threads the durable queue → worker. See §11.

✅ **GAM session-topic trigger** (rev 52) — `store()` tracks a per-session running
`topic_embedding` (new `sessions` collection, EWA-blended); when a turn's cosine drops
below `topic_shift_threshold` (0.7) it **flushes the working buffer to episodic** and
flags the session `consolidation_due` (emits a `topic_shift` metric). Signal-only +
inline (reuses the already-computed turn embedding); Dream State stays a separate pass.
See §13.

✅ **EWA edge weights** (rev 53) — `relates_to` edge `weight` is now a recency-decayed
exponentially-weighted average updated in the `_RELATE` upsert (was a constant 1.0):
`α·1 + (1−α)·OLD.weight·exp(−λ·Δt)` (seed α). The graph retrieval arm folds the mean
bridging-edge weight into the bridge salience (`MAX(belief, centrality, weight)`) so
recently-confirmed relations rank higher (§12), and it's surfaced in `/v1/graph` edges.

✅ **Dedicated embedding cache** (rev 54) — a process-level per-tenant LRU
(`embedding_cache.py`, `embed_cached()`) memoizes `embed(text)` so recurring entity
names, repeated queries, and idempotent replays skip the provider. Keyed by
`(tenant_id, model, version, dimensions, text-hash)` (per-tenant = §24 timing-attack
defense); distinct from the HyDE/gate `QueryCache`. Exposes `hit_rate` + an
`embedding_cache` metric (+ OTEL meter). See §16.

✅ **Hallucination / Noise-Reduction eval** (rev 55) — `eval/halu.py` adds the full
agent-loop harness the deterministic LoCoMo runner couldn't: retrieve →
`generate_answer` → `judge_answer`, with an injectable generator + judge (keyless in
CI, Haiku for real). Reports **Hallucination Rate** (unsupported claims) +
**Noise-Reduction Rate** (stayed focused on the relevant fact); report-only with
optional `--max-hallucination` / `--min-nrr` gates. CLI mirrors `benchmark.py`. See §23.

✅ **Lazy decay at query time** (rev 48) — the Ebbinghaus multiplier
`strength·exp(-λ·Δt)` is folded into the **BM25 + graph retrieval arm SORTs** in
AQL, so freshness shapes candidate *selection* (pool membership), not only the
post-fusion reorder it already did. The vector arm stays index-pure
(`APPROX_NEAR_COSINE` must be the sole sort or it loses index acceleration), reached
by the retained uniform post-fusion pass that weights final magnitude (RRF discards
per-arm score magnitude). The scheduled `decay_sweep` remains for hard `invalid_at`
soft-deprecation.

#### Prioritized (next up)

*Empty — all prioritized items shipped. Promote from the backlog as needed.*

#### Backlog (unprioritized)

- **Real-data benchmark run (LoCoMo)** — the harnesses exist ([benchmark.py](../core/src/arango_memory/eval/benchmark.py),
  [halu.py](../core/src/arango_memory/eval/halu.py)); this is the BYO-dataset run + the
  glue around it (§23):
  - ✅ **Dataset converter** `eval/locomo_convert.py` (rev 70) — maps real LoCoMo
    (`session_N` keys, `evidence`/category codes) → our `load_dataset` schema. Derives
    `gold_fact` from the **cited evidence turn** (not the synthesized answer, or Recall@k
    is unfairly punished by paraphrase); maps category codes → single/multi-hop/temporal;
    **adversarial (cat 5) + evidence-less Qs excluded + counted**. Unit-tested on a
    schema fixture; `locomo10.json`/converted output are gitignored. Still: hand-verify
    ~10 converted samples before trusting a run.
  - **Real providers**: `EMBEDDING_PROVIDER=openai` (+ key); full mode + halu need
    `GENERATION_PROVIDER=anthropic` (+ key); a real ArangoDB with the vector index
    trained (LoCoMo crosses `VECTOR_N_LISTS`).
  - **Run + interpret**: lite first (isolates retrieval), then full + halu. Treat
    **Recall@k + tokens-injected** as the primary signals; top-hit token-F1 reads low
    on synthesized answers, so report it as directional. Watch the per-category
    breakdown (multi-hop/temporal vs single-hop).
  - **Repeatability**: `make benchmark` target + `docs/benchmarking.md` (acquisition +
    license note — LoCoMo is BYO/externally licensed, **never commit it**; gitignore
    `eval/datasets/*.json`). Optional: capture core-retrieval p99 latency (§23 targets)
    — the runner doesn't time it yet.
  - First move: converter + a hand-verified 10-sample lite run on real embeddings,
    before spending on full-mode LLM calls. Keyless-testable on a synthetic
    LoCoMo-shaped fixture so the converter lands CI-green without the real dataset/keys.

- **Hardening → deployable service.** The core is feature-complete but carries the
  keyless/demo posture; these turn it production-ready. Suggested order:
  **auth → durable queue → index/latency audit.**
  - **Tier 1 — security + reliability (gates deployability):**
    - ✅ **Authentication** (rev 56) — static **bearer API keys** (`security/auth.py`,
      `API_KEYS` config: `key → {tenant_id, scope}`). Open by default (keyless
      dev/CI/demo); when configured, `/v1` requires `Authorization: Bearer <key>`
      (`401`/`/health` exempt) and `tenant_id` + `access_level` are **derived from the
      verified key**, not the body (`403` on tenant mismatch / read-key write), closing
      the self-assertion hole. Threaded through the Vercel / dungeon / MCP clients.
      *(JWT/OIDC is the follow-on below.)*
    - ✅ **JWT / OIDC authentication** (rev 71) — `jwt_auth.verify_jwt` validates a
      signed token from an external IdP (JWKS, RS256 allowlist, `exp`/`nbf`/`iss`/`aud`,
      claims → tenant/scope) and yields the same `Principal`; `require_principal`
      dispatches JWT-or-static so it coexists with bearer keys. Fail-closed (a JWKS
      error is `401`); revocation by expiry only (short-TTL — no denylist). Hardening
      tests cover `alg:none`/non-allowlisted-alg rejection, JWKS-unreachable, clock-skew
      leeway, and JWT-path multi-tenant isolation (crafted tenant claim → `403`).
    - ✅ **Rate limiting + request-size caps** (rev 58) — a `Content-Length` middleware
      rejects bodies over `MAX_REQUEST_BYTES` (1 MiB, on) with `413`; a `rate_limit`
      dependency (after auth) throttles per tenant / IP over `RATE_LIMIT_PER_MINUTE`
      (`0`=off, opt-in) with `429`+`Retry-After`. In-process by default; **set `REDIS_URL`
      for one shared cross-instance budget** (rev 73, `RedisRateLimiter`, fail-open).
    - ✅ **Durable queue** (rev 57) — `ArangoQueue` (a `write_intents` collection)
      behind the `WriteQueue` Protocol, selected by `WRITE_QUEUE_BACKEND=arango`:
      claim→ack leasing survives a crash between accept and commit (redelivers on
      lease expiry; at-least-once, idempotency-safe). `memory` stays the dev/CI
      default. **Redis/SQS** remain drop-in adapters behind the same Protocol.
    - ✅ **Multi-instance** (rev 59) — the API is stateless; run N instances over a
      shared `arango` queue + DB (exclusive-locked `claim` prevents double-processing).
      The rate limiter + embedding cache are per-instance by default; **`REDIS_URL`**
      shares them across instances (rev 73 — global limiter budget + shared embedding
      cache, both fail-soft). The query cache + `/health` latency window stay
      per-instance. Documented in ops.md.
    - ✅ **Structured logging + correlation IDs** (rev 59) — stdlib JSON/text logs
      (`LOG_FORMAT`/`LOG_LEVEL`) on the `arango_memory` logger; a `RequestLogMiddleware`
      assigns/echoes `X-Request-ID` and access-logs each request; every line (incl.
      worker dead-letter + degraded retrieve) carries `request_id` + `tenant` via
      contextvars. No new dependency. (§18)
  - **Tier 2 — performance & correctness:**
    - ✅ **AQL index audit** (rev 60) — persistent scope indexes back every hot-path
      `tenant_id`/`agent_id`/`invalid_at` filter (memories/entities/episodes/
      write_intents/ontology_proposals); `python -m arango_memory.ops explain` EXPLAINs
      the hot queries and flags any full collection scan.
    - ✅ **Latency percentiles** (rev 61) — an in-process `LatencyRecorder` keeps a
      rolling window of recent op latencies and reports p50/p95/p99 per mode
      (`retrieval.lite`/`.full`/`write`) on `/health.latency_ms`, so tail latency is
      checkable against the §23 targets without an OTEL exporter (the duration
      histograms still feed one when configured). Per-instance window.
    - ✅ **Batch embedding** (rev 62) — a write's distinct entity names are embedded in
      one `embed_batch` provider call via `embed_batch_cached` (cache-aware: only
      misses are sent, per-name hit/miss metrics preserved), instead of one call per
      name. Collapses N provider round-trips per write to ≤1.
  - ✅ **Tier 3 — testing depth** (rev 63–65): concurrency / multi-tenant isolation under
    load (§22); failure injection (embedder outage → BM25-only, DB-unreachable write →
    dead-letter + replay); authz breadth (cross-tenant `403` across every tenant-scoped
    read, `test_auth.py`); a deterministic perf-regression gate (≤1 embed batch/write, AQL
    arm count doesn't grow with the corpus, `test_perf_invariants.py`); FakeEmbedder
    decoupling (shared `StubEmbedder` with explicit geometry for the entity-merge and
    topic-shift threshold bands).
  - ✅ **Tier 4 — release & DX** (rev 66–69): semver + CHANGELOG; FastAPI `/docs` (OpenAPI)
    surfaced (tagged routes, public even under auth); a sample OTEL collector + Grafana
    dashboard (`deploy/observability/`); a **gated release pipeline** (`release.yml`) that
    builds the core wheel + `@arango-memory/vercel` tarball + container image with a
    CycloneDX SBOM + dep-scan each — publishing no-ops until registry credentials are
    added (machinery in place, nothing pushed). Full package metadata + MIT `LICENSE`.

### Production-readiness punch list (rev 74)

A full assessment (2026-06-19) found the project **production-*grade* but not yet
production-*proven***: the engineering (durability, multi-tenant isolation, auth,
observability, graceful degradation) is complete and verified green (306 tests, mypy
`--strict`, ruff, secret scan), but two things stand between it and a confident
"production ready" sign-off — **empirical validation** and a **deploy-hardening pass**.
Open items, prioritized:

- **P1 — material for sign-off:**
  - **Prove the §23 targets on real data.** F1 ≥ 0.65 / Recall@k / tokens ≤ 1500 and the
    latency p99s (core ≤ 200ms, lite ≤ 250ms) have only run on the smoke slice; the perf
    gate is *structural* (call-count invariants), not wall-clock. Do the BYO LoCoMo run
    (`eval/locomo_convert` → `eval/benchmark`, §23) + a real latency measurement on a warm
    vector index. Memory *quality* is currently unproven.
  - ✅ **Container hardening** (rev 76, `core/Dockerfile`): non-root `USER` (uid 10001),
    `HEALTHCHECK` on `/health`, base pinned by digest; a CI image build-smoke catches
    Dockerfile regressions on every PR.
  - ✅ **Liveness vs readiness split** (rev 76): `/health` is liveness (always `200`, not
    DB-gated); new `/ready` is readiness (`200`/`503` on DB reachability). Both public.
- ✅ **P2 — accuracy / robustness** (rev 77):
  - §17 reworded: embedding **encryption-at-rest is a storage-layer concern** (ArangoDB
    Enterprise / disk encryption), documented in ops.md; the app-level defenses are
    per-tenant cache namespacing + never-returning embeddings.
  - §18 metrics block reconciled to the **actually-emitted** OTEL instruments (the spec's
    `memory.retrieval.llm_calls` / `memory.embedding.cache_hit_rate` gauge were never
    emitted; hit-rate is a counter ratio).
  - Cross-field config validation: `create_app` logs a startup **warning** when
    `OIDC_ISSUER` is set without `OIDC_AUDIENCE` (the `aud` claim would be unverified).
- **P3 — nice to have:** a load/soak test (sustained-load latency, not just concurrency
  correctness); a drilled backup/restore (DR) path beyond the documented `arangodump`.

*Deferred by design (not defects):* full Next.js chat UI (Step 3.5c), Redis-backed write
queue, JWT denylist (short-TTL chosen), adapter token-acquisition helper (pass-through chosen).

*Shipped in v2:* MCP server, LangChain/LangGraph, CrewAI (+ G-Memory tiers), the
full §19 entity API, Step 3e extraction tier, the Memory Dungeon reference app.

*Not adopted (from adjacent prior art):* LangGraph for the core (contradicts the
lite-mode zero-hot-path-LLM envelope, §10), a POI-specific domain schema, and a
React/Cytoscape UI baked into the core — out of scope for a domain-agnostic
memory backend.

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

### Versioning & releases (rev 65)
- **SemVer.** During `0.x`, minor versions may carry breaking changes; from `1.0.0`
  the usual major/minor/patch contract holds. The Python core (`arango-memory`) and
  the Vercel adapter (`@arango-memory/vercel`) are **versioned and released together**.
- **Single source of truth:** the version lives once in `core/pyproject.toml`.
  `arango_memory.__version__` reads it at runtime via `importlib.metadata`, and the
  FastAPI app (`/openapi.json`, `/docs`) reports that same value — no hardcoded
  duplicates to drift.
- **`CHANGELOG.md`** (Keep a Changelog) at the repo root is the human release log;
  an `[Unreleased]` section accrues entries that become the next tagged release.
- **Release pipeline** (rev 69, `.github/workflows/release.yml`): a `v*` tag builds the
  core wheel/sdist, the adapter npm tarball, and the container image; emits a
  **CycloneDX SBOM** + a dependency scan (pip-audit / npm audit) for each; and uploads
  them as artifacts. **Publishing is gated** — PyPI/npm steps no-op until
  `PYPI_API_TOKEN`/`NPM_TOKEN` secrets exist, and the image pushes to GHCR only when
  the `PUBLISH_IMAGE` repo variable is `true`. Both packages carry full metadata
  (license `MIT`, URLs, classifiers/keywords); the repo `LICENSE` is MIT.

---

*End of Design Specification (rev 3)*
