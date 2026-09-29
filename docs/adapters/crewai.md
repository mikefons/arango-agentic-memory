# CrewAI Adapter

`arango_memory.crewai` — **in-process Python** CrewAI integration over the core. There are
two entry points, by crewai version:

| crewai | Entry point | Plugs into |
|---|---|---|
| **≥ 1.10** | `arango_crewai_memory()` | `Crew(memory=...)` (crewai's unified memory) |
| < 1.10 | `crew_memory()` + `to_crewai_storage()` | `Crew(external_memory=ExternalMemory(...))` (legacy) |

`ArangoCrewStorage` / `crew_memory()` themselves are `crewai`-free and work with any version
(e.g. from crew tools).

## Install
```bash
pip install "arango-memory[crewai]"
```

## crewai ≥ 1.10 — unified memory
```python
from crewai import Crew
from arango_memory.client import ArangoMemoryClient
from arango_memory.crewai import arango_crewai_memory

db = ArangoMemoryClient().connect()
memory = arango_crewai_memory(db, tenant_id="acme", agent_id="research-crew")
crew = Crew(agents=[...], tasks=[...], memory=memory)

# Or directly:
memory.remember("Decision: ship on Friday", scope="/research", categories=["decisions"],
                importance=0.8)
memory.recall("when do we ship?")
```
`arango_crewai_memory(...)` returns a regular crewai `Memory`; extra keyword arguments go to it
(`llm=`, `root_scope=`, `recency_weight=`, …). crewai still does its own LLM analysis on save
(scope/categories/importance inference, consolidation), so the crew needs an LLM configured as
usual — unless you pass `scope`, `categories` and `importance` yourself, which is crewai's
zero-LLM save path.

**How it keeps hybrid retrieval.** crewai's storage interface hands `search()` only a query
*embedding*. The core's retrieval also needs the *text* (BM25 and the entity graph), so the
returned `Memory` uses a **paired embedder**: the core's embedder, which remembers the text behind
each vector it produces. `search()` recovers the query text and runs the core's full retrieval
(BM25 + vector + graph, plus rerank if enabled); a vector the embedder didn't produce falls back to
exact cosine search. Build the backend and embedder together (`arango_crewai_memory` does) — a
`Memory` given `ArangoMemoryBackend` with a different embedder degrades to vector-only search.

**Where the data lives.** Each crewai record is one core memory (text, embedding, graph —
PII-redacted by the core's store path) plus a `crewai_records` row for crewai's own fields (id,
scope, categories, metadata, importance, timestamps, source, private). Scopes are path prefixes
(`/crew/a` covers `/crew/a/x`, not `/crew/ab`). Tenant `forget` hides a tenant's records at once;
`purge` removes the rows.

**Scores** are cosine similarity in [0, 1] (as crewai's Qdrant backend reports), which is what
crewai's consolidation threshold and its recency/importance composite expect. The core decides
*which* memories are candidates; crewai's recall blends recency and importance on top.

## crewai < 1.10 — legacy Storage shim
`crew_memory()` builds a G-Memory 3-tier store (DESIGN.md §14) for one agent:
- **interaction** — the agent's private memory (its own `agent_id`)
- **query** — shared crew memory (`<crew_id>::query`, all members read/write)
- **insight** — distilled strategy (`<crew_id>::insight`, read-only here; only the
  Dream State path writes it)

```python
from arango_memory.crewai import crew_memory, to_crewai_storage

mem = crew_memory(db, tenant_id="acme", crew_id="research", agent_id="analyst")

# Direct (crewai-free) — save/search/reset over the core's hybrid retrieve:
mem.query.save("Decision: ship on Friday")
hits = mem.query.search("when do we ship?")        # [{context, score, metadata}]

# Wire into CrewAI < 1.10:
from crewai import Crew
from crewai.memory.external.external_memory import ExternalMemory
crew = Crew(..., external_memory=ExternalMemory(storage=to_crewai_storage(mem.query)))
```
- `ArangoCrewStorage` speaks the text contract `save(value, metadata)` /
  `search(query, limit, score_threshold)` / `reset()`, mapping onto the core's hybrid retrieve.
  `reset()` is a `forget` soft-delete; results exclude embeddings (§17).
- The shim ignores CrewAI's per-call `score_threshold` (our fused scores live on a different
  scale) — `limit` is the cutoff.
- On crewai ≥ 1.10, `to_crewai_storage()` raises an `ImportError` pointing to
  `arango_crewai_memory()`.

## Testing
`make test-crewai` (in `core/`) runs the adapter tests and mypy against a real crewai install in
its own venv; CI runs it as the `crewai` job.
