# CrewAI Adapter

`arango_memory.crewai` — **in-process Python**: a shared-crew memory store that
realises the **G-Memory 3-tier** (DESIGN.md §14) via `agent_id` namespacing. The
storage logic is `crewai`-free (testable directly); the `crewai.Storage` shim is a
thin wrapper.

## Install
```bash
pip install "arango-memory[crewai]"   # the shim needs the `crewai` extra; the store logic does not
```

## Tiers
`crew_memory()` builds three storages for one agent:
- **interaction** — the agent's private memory (its own `agent_id`)
- **query** — shared crew memory (`<crew_id>::query`, all members read/write)
- **insight** — distilled strategy (`<crew_id>::insight`, read-only here; only the
  Dream State path writes it)

## Usage
```python
from arango_memory.client import ArangoMemoryClient
from arango_memory.crewai import crew_memory, to_crewai_storage

db = ArangoMemoryClient().connect()
mem = crew_memory(db, tenant_id="acme", crew_id="research", agent_id="analyst")

# Direct (crewai-free) — save/search/reset over the core's hybrid retrieve:
mem.query.save("Decision: ship on Friday")
hits = mem.query.search("when do we ship?")        # [{context, score, metadata}]

# Wire into CrewAI (needs the extra, crewai<1.10 — see Notes):
from crewai import Crew
from crewai.memory.external.external_memory import ExternalMemory
crew = Crew(..., external_memory=ExternalMemory(storage=to_crewai_storage(mem.query)))
```

## Notes
- `ArangoCrewStorage` speaks the stable text contract `save(value, metadata)` /
  `search(query, limit, score_threshold)` / `reset()`, mapping onto the core's
  hybrid retrieve (raw text in → BM25 + vector + graph). `reset()` is a `forget`
  soft-delete; results exclude embeddings (§17).
- The shim ignores CrewAI's per-call `score_threshold` (our fused scores live on a
  different scale) — `limit` is the cutoff.
- **CrewAI ≥ 1.10 is not supported by the shim.** CrewAI 1.10.0 removed the legacy
  `Storage` interface and `ExternalMemory` in favour of its unified memory, so
  `to_crewai_storage()` needs `crewai<1.10`; on newer crewai it raises an
  `ImportError` explaining this. `ArangoCrewStorage` / `crew_memory()` don't import
  crewai and work with any version — call them directly (e.g. from crew tools). A
  port to CrewAI's unified-memory backend is planned for 0.2.
