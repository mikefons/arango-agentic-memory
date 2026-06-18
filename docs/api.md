# Core API Reference

The ArangoDB agentic-memory **core** exposes one stable contract over two surfaces:

- **HTTP `/v1`** — a FastAPI service (the boundary the Vercel TS adapter and any
  remote client use). Base URL defaults to `http://localhost:8080`.
- **In-process Python** — the same operations as importable functions (used by the
  in-process LangChain/LangGraph and CrewAI adapters, and the MCP server).

Keeping this contract neutral is what makes adapters additive rather than refactors
(DESIGN.md §19). All requests/responses are JSON (`content-type: application/json`).

**Interactive docs (OpenAPI).** The running service self-describes — the live, version-
stamped spec is the source of truth this page mirrors:

- **Swagger UI:** `GET /docs` · **ReDoc:** `GET /redoc` · **raw schema:** `GET /openapi.json`

Routes are grouped by tag (ingestion, retrieval, entities & graph, lifecycle, memory
ops, system). These three paths stay **public even when `API_KEYS` is enforced** (and
are exempt from rate limiting), so the docs are always reachable.

> See also: [DESIGN.md](DESIGN.md) for the architecture; section refs below (§N) point into it.

---

## Access context & ABAC (§17)

Every memory operation is scoped by an **access context**:

```jsonc
{
  "tenant_id": "acme",          // required — hard isolation boundary; every query binds it
  "agent_id":  "assistant-1",   // required — per-agent scope
  "session_id": "run-42",       // optional
  "access_level": "read"        // "read" (default) | "write"
}
```

**Mutating endpoints require write access** or they return **`403`**:
`/v1/store`, `/v1/step`, `/v1/forget`, `/v1/seed`, `/v1/supersede`, `/v1/dream`,
`/v1/salience`, `/v1/community`, `/v1/ontology/*`. Reads (`/v1/retrieve`, `/v1/entity*`, `/v1/graph`, `/v1/steps`,
`/v1/stats`) allow read.

---

## Authentication (§17)

Static **bearer API keys**, configured on the core via `API_KEYS` (a JSON map
`key → {tenant_id, scope}`, scope `read`|`write`). Send `Authorization: Bearer <key>`.

- **Open by default** — when no keys are configured the core runs **open**: the
  request's `tenant_id`/`access_level` are trusted (the keyless dev/CI/demo posture).
- **Enforced** — once `API_KEYS` is set, every `/v1` route needs a valid key (`401`
  otherwise; `/health` stays public). The caller's **identity comes from the key**:
  the request's `tenant_id` must match the key's tenant (else `403`), and a write
  needs a `write`-scoped key — the body can no longer assert identity or escalate.
- Clients pass it through: Vercel adapter `arangoMemory({ apiKey })`, the dungeon
  `CORE_API_KEY`, the MCP server `ARANGO_MEMORY_API_KEY`.
- *(JWT/OIDC is a roadmap follow-on; bearer keys are the dependency-free default.)*

**Abuse limits (§17).** Bodies over `MAX_REQUEST_BYTES` (default 1 MiB) → **`413`**.
With `RATE_LIMIT_PER_MINUTE > 0`, requests over the per-tenant (or per-IP, open mode)
budget → **`429`** with a `Retry-After` header. `/health` is exempt from both.

---

## Conventions

- **Durable, idempotent writes (§15).** `/v1/store` and `/v1/step` **enqueue** an
  idempotency-keyed intent and return immediately (`status: "queued"`); a
  background worker commits to ArangoDB with retry + dead-letter. Re-sending an
  identical turn cannot create duplicates. Returned ids are deterministic from the
  key, so they're known before the commit lands (entities resolve asynchronously).
- **Embeddings are never returned (§17).** No entity/memory projection includes
  vector embeddings — an inversion-attack defense.
- **Memory never breaks the caller (§15).** `/v1/retrieve` degrades to empty
  context on any fault rather than erroring.
- **Errors.** `403` write-access required · `404` entity not found · `422`
  validation (malformed body) · `503` core/DB unavailable (health).

---

## HTTP endpoints

### `GET /health`
Liveness + DB reachability + process-global latency (p50/p95/p99 ms per op over a
rolling in-process window; empty until traffic flows). See §23 targets.
```json
{ "status": "ok", "arango": true, "mode": "lite",
  "latency_ms": { "retrieval.lite": { "count": 128, "p50": 41.0, "p95": 88.0, "p99": 120.0 } } }
```

### Ingestion

#### `POST /v1/store` · *write*
Persist one turn (episode + episodic memory + extracted entities/relations).
```jsonc
// request
{
  "content": "Alice moved to Berlin in 2019",
  "ctx": { "tenant_id": "acme", "agent_id": "a", "access_level": "write" },
  "turn_index": 0,                // optional, default 0 (part of the idempotency key)
  "source_reliability": 1.0,      // optional 0..1 (§8/§12) — weights corroboration → belief
  "memory_type": "episodic"       // optional: "episodic" | "working" (§5/§14) — working
                                  //   memory is session-scoped (TTL) + SCM-capped, mints no entities
}
// response
{ "status": "queued", "episode_id": "<key>", "memory_ids": ["<key>-mem"] }
```

#### `POST /v1/step` · *write*
Record a completed tool call as procedural memory (§11). Reuse bumps `use_count`.
```jsonc
// request
{
  "tool_name": "search",
  "arguments": { "q": "weather" },
  "outcome": "success",            // "success" | "failure"
  "ctx": { "tenant_id": "acme", "agent_id": "a", "access_level": "write" },
  "pattern_summary": "",           // optional
  "source_memory_key": null,        // optional → TOUCHED edge
  "prev_step_key": null             // optional → TRANSITION edge (chains steps)
}
// response
{ "status": "queued", "step_id": "<key>" }
```

#### `GET /v1/steps` · *read*
`?tenant_id=&agent_id=&tool_name=(optional)&limit=20` → procedural memories,
most-reused first.
```json
{ "steps": [ { "tool_name": "search", "outcome": "success", "use_count": 3, "arguments": {} } ] }
```

### Retrieval

#### `POST /v1/retrieve` · *read*
Hybrid retrieval (BM25 + vector + graph → RRF → MMR → tiered token budget, §9).
```jsonc
// request
{
  "query": "where does Alice live?",
  "ctx": { "tenant_id": "acme", "agent_id": "a" },
  "opts": {                         // all optional
    "mode": "lite",                 // "lite" | "full" (full adds HyDE + adaptive gate)
    "max_memory_tokens": 1500,
    "k": 10,
    "n_probe": 10
  }
}
// response
{
  "context": "…assembled, token-budgeted context block…",
  "hits": [ { "text": "Alice moved to Berlin in 2019", "score": 0.031, "source": "graph" } ],
  "tokens_injected": 64
}
```
`source` ∈ `bm25 | vector | graph`. On any fault the response is empty
(`context: ""`, `hits: []`) — never an error.

### Semantic memory (entities & relations)

#### `GET /v1/entity` · *read*
`?entity_id=&tenant_id=` → one entity + its `relates_to` neighbours. **`404`** if
absent or forgotten. Embeddings excluded.
```json
{
  "entity": { "id": "…", "name": "Alice", "label": "Person", "confidence": 1.0,
              "belief": 0.875, "centrality": 1.0, "mention_count": 3,
              "needs_review": false, "conflict_with": null, "source": "observed", "summary": "" },
  "related": [ { "id": "…", "name": "Berlin", "label": "Location", "relationship": "associated_with" } ]
}
```

#### `GET /v1/entities` · *read*
`?tenant_id=&agent_id=(opt)&label=(opt)&limit=50` → the tenant's entities
(superseded ones excluded), most-mentioned first.
```json
{ "entities": [ { "id": "…", "name": "Alice", "label": "Person", "belief": 0.875, "centrality": 1.0 } ] }
```

#### `GET /v1/graph` · *read*
`?tenant_id=` → the **full semantic graph** for visualization — entities
(**including superseded**, carrying `invalid_at`) + `relates_to`/`Supersedes` edges.
```json
{
  "nodes": [ { "id": "…", "name": "Alice", "label": "Person", "belief": 0.875,
               "centrality": 1.0, "invalid_at": null, "needs_review": false } ],
  "edges": [ { "source": "…", "target": "…", "relationship": "associated_with",
               "kind": "relates_to", "corroboration": 2, "belief": 0.75 } ]
}
```

#### `POST /v1/seed` · *write*
Cold-start: one seed entity per profile item (source `seed`, confidence `0.6`),
never clobbering observed facts (§11).
```jsonc
// request
{ "profile": { "role": "analyst", "domain": "logistics", "preferences": ["sql", "vim"] },
  "ctx": { "tenant_id": "acme", "agent_id": "a", "access_level": "write" } }
// response
{ "status": "seeded", "entity_ids": ["…", "…"] }
```

#### `POST /v1/supersede` · *write*
Record `new` superseding `old` (bi-temporal, §12): writes a `Supersedes` edge and
soft-deprecates `old` (`invalid_at`).
```jsonc
{ "new_key": "…", "old_key": "…",
  "ctx": { "tenant_id": "acme", "agent_id": "a", "access_level": "write" } }
// → { "status": "superseded" }
```

### Lifecycle

#### `POST /v1/dream` · *write*
Run Dream State consolidation (§13): review flagged/well-attested entities, confirm
conflicts → supersede (better-attested survives), distill summaries; circuit breaker.
```jsonc
{ "ctx": { "tenant_id": "acme", "agent_id": "a", "access_level": "write" } }
// → { "reviewed": 4, "superseded": 1, "consolidated": 2, "cleared": 0, "breaker_tripped": false }
```

#### `POST /v1/salience` · *write*
Recompute PageRank `centrality` (0..1, hub = 1.0) over the tenant's entity subgraph,
in-process (§9; Pregel was removed in ArangoDB 3.12).
```jsonc
{ "ctx": { "tenant_id": "acme", "agent_id": "a", "access_level": "write" } }
// → { "entities": 37 }
```

#### `POST /v1/community` · *write*
Recompute label-propagation `community` labels (dense integers) over the tenant's
entity subgraph, in-process (§9/§13). Surfaced on entity/graph reads; used to scope
Dream State conflict review to same-community pairs.
```jsonc
{ "ctx": { "tenant_id": "acme", "agent_id": "a", "access_level": "write" } }
// → { "entities": 37, "communities": 5 }
```

#### `POST /v1/ontology/scan` · *write* · *flag-gated*
Propose typed relationships from recurring `associated_with` clusters (§13). **404
unless `ONTOLOGY_EVOLUTION=true`**; needs a real generator to produce useful labels.
Records proposals; never mutates the graph.
```jsonc
{ "ctx": { "tenant_id": "acme", "agent_id": "a", "access_level": "write" } }
// → { "clusters": 4, "proposed": 2 }
```

#### `GET /v1/ontology/proposals` · *read* · *flag-gated*
List relationship proposals for review. Params: `tenant_id`, optional `status`
(`pending`/`approved`/`rejected`).
```jsonc
// → [ { "label_a": "Company", "label_b": "Person", "proposed_relationship": "works_at",
//       "support": 7, "status": "pending", ... } ]
```

#### `POST /v1/ontology/approve` · *write* · *flag-gated*
Approve a proposal → relabel the tenant's matching `associated_with` edges to the
proposed type. `POST /v1/ontology/reject` marks it rejected (no graph change).
```jsonc
{ "ctx": { "tenant_id": "acme", "agent_id": "a", "access_level": "write" }, "key": "acme__Company__Person" }
// → { "status": "approved", "relationship": "works_at", "relabeled": 7 }
```

### Administration

#### `POST /v1/forget` · *write*
Right to be forgotten (§17): soft-delete (set `invalid_at`) a tenant's — or one
agent's — memories + entities. Note: ABAC field is top-level here, not under `ctx`.
```jsonc
{ "tenant_id": "acme", "agent_id": null, "access_level": "write" }
// → { "status": "forgotten", "counts": { "memories": 12, "entities": 9 } }
```
*(Hard delete — `purge` — is an ops-only callable, not an HTTP endpoint.)*

#### `GET /v1/stats` · *read*
`?tenant_id=` → per-tenant collection counts.
```json
{ "counts": { "episodes": 20, "memories": 20, "entities": 37, "relates_to": 52, "steps": 8 } }
```

---

## In-process Python API

The same operations, importable — what the in-process adapters and the MCP tools
call directly (no HTTP hop). All take a connected `StandardDatabase` and are
keyword-only. Selected signatures:

```python
from arango_memory.client import ArangoMemoryClient
db = ArangoMemoryClient().connect()          # env-driven (ARANGO_URL, ARANGO_DB, …)

# Ingest
from arango_memory.ingest.store import store
store(db, content="…", tenant_id="t", agent_id="a",
      session_id=None, turn_index=0, mode="lite",
      message_type=None, source_reliability=1.0,
      memory_type="episodic")                             # → StoreResult(episode_id, memory_ids, entity_ids)

from arango_memory.ingest.procedural import record_step, get_steps
record_step(db, tool_name="search", arguments={}, outcome="success",
            tenant_id="t", agent_id="a", prev_step_key=None)   # → step key
get_steps(db, tenant_id="t", agent_id="a", tool_name=None, limit=20)

# Retrieve
from arango_memory.retrieve.search import retrieve
retrieve(db, query="…", tenant_id="t", agent_id="a",
         k=10, max_memory_tokens=1500, mode="lite")        # → RetrieveResult(context, hits, tokens_injected)

# Semantic memory
from arango_memory.entity_api import get_entity, list_entities, seed
from arango_memory.graph_api import tenant_graph
from arango_memory.lifecycle.conflict import supersede
supersede(db, new_key="…", old_key="…")

# Lifecycle
from arango_memory.lifecycle.dream import run_dream_state          # → DreamResult
from arango_memory.lifecycle.salience import compute_centrality, pagerank
from arango_memory.lifecycle.community import compute_communities, label_propagation
from arango_memory.lifecycle.ontology import propose_relationship_types, approve_proposal
from arango_memory.lifecycle.decay import decay_sweep
from arango_memory.security.forget import forget, purge
```

### Pluggable providers (keyless by default)

The hot path is provider-agnostic; defaults are **deterministic fakes** so dev/CI
need no API keys (`get_*` factories select from `Settings`):

| Protocol | Fake (default) | Real |
|---|---|---|
| `Embedder` (`embedding.py`) | `FakeEmbedder` | `OpenAIEmbedder` |
| `Generator` (`generation.py`) | `FakeGenerator` | `AnthropicGenerator` (Haiku) |
| `Extractor` (`ingest/extract.py`) | `FakeExtractor` | `SpacyExtractor` · `GlinerExtractor` · `HaikuExtractor` · `LayeredExtractor` |

Selected via env (`EMBEDDING_PROVIDER`, `GENERATION_PROVIDER`, `EXTRACTION_PROVIDER`).

### Ops CLI

`python -m arango_memory.ops {vector-rebuild | embeddings-migrate | replay}`.
