"""CrewAI adapter (DESIGN.md §21) — in-process shared-crew memory over the core.

Like the LangChain adapter, this is in-process Python (no HTTP hop). It exposes a
shared crew memory store that realises the G-Memory 3-tier (§14) via `agent_id`
namespacing:

    from arango_memory.crewai import crew_memory, to_crewai_storage

    # crewai >= 1.10 (unified memory):
    crew = Crew(..., memory=arango_crewai_memory(db, tenant_id="t", agent_id="research"))

    # crewai < 1.10 (legacy Storage shim):
    mem = crew_memory(db, tenant_id="t", crew_id="research", agent_id="analyst")
    crew = Crew(..., external_memory=ExternalMemory(storage=to_crewai_storage(mem.query)))

`crew_memory`/`ArangoCrewStorage` are crewai-free; `arango_crewai_memory` and
`to_crewai_storage` require the `crewai` extra.
"""

from __future__ import annotations

from typing import Any

from .shim import to_crewai_storage
from .storage import ArangoCrewStorage, CrewMemory, crew_memory

# crewai ≥ 1.10 unified memory (CW-1) imports crewai at module load, so it's resolved lazily:
# `import arango_memory.crewai` stays crewai-free.
_UNIFIED = ("ArangoMemoryBackend", "PairedEmbedder", "arango_crewai_memory")


def __getattr__(name: str) -> Any:
    if name in _UNIFIED:
        from . import unified

        return getattr(unified, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ArangoCrewStorage",
    "ArangoMemoryBackend",
    "CrewMemory",
    "PairedEmbedder",
    "arango_crewai_memory",
    "crew_memory",
    "to_crewai_storage",
]
