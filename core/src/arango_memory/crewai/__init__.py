"""CrewAI adapter (DESIGN.md §21) — in-process shared-crew memory over the core.

Like the LangChain adapter, this is in-process Python (no HTTP hop). It exposes a
shared crew memory store that realises the G-Memory 3-tier (§14) via `agent_id`
namespacing:

    from arango_memory.crewai import crew_memory, to_crewai_storage

    mem = crew_memory(db, tenant_id="t", crew_id="research", agent_id="analyst")
    crew = Crew(..., external_memory=ExternalMemory(storage=to_crewai_storage(mem.query)))

`crew_memory`/`ArangoCrewStorage` are crewai-free; `to_crewai_storage` (the
`crewai.Storage` shim) requires the `crewai` extra.
"""

from __future__ import annotations

from .shim import to_crewai_storage
from .storage import ArangoCrewStorage, CrewMemory, crew_memory

__all__ = [
    "ArangoCrewStorage",
    "CrewMemory",
    "crew_memory",
    "to_crewai_storage",
]
