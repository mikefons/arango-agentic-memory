"""`ArangoCrewStorage` — crewai-free shared-crew memory over the core (§14, §21).

This module holds NO `crewai` import, so the storage logic is testable against
the core directly (the `crewai.Storage` shim lives in `shim.py`). It speaks the
legacy text-based storage contract — `save(value, metadata)` /
`search(query, limit, score_threshold)` / `reset()` — which maps cleanly onto the
core's hybrid `retrieve()` (raw text in) without bypassing BM25/graph fusion.

The G-Memory 3-tier layout (§14) is realised purely through `agent_id`
namespacing within a tenant (the schema already supports it — no core change):

- **interaction** — an agent's private working/episodic memory (its own `agent_id`).
- **query**       — shared crew memory all agents read/write (`<crew_id>::query`).
- **insight**     — distilled strategy, shared + read-only here; only the Dream
  State consolidation path writes it (`<crew_id>::insight`).

Embeddings are never returned (§17); `reset()` is a soft-delete (`forget`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from arango.database import StandardDatabase

from ..embedding import Embedder
from ..generation import Generator
from ..ingest.store import store
from ..retrieve.search import retrieve
from ..security.forget import forget


class ArangoCrewStorage:
    """Text-based memory storage for a CrewAI crew, bound to (tenant, agent)."""

    def __init__(
        self,
        db: StandardDatabase,
        *,
        tenant_id: str,
        agent_id: str,
        mode: str = "lite",
        k: int = 5,
        max_memory_tokens: int = 1500,
        score_threshold: float = 0.0,
        read_only: bool = False,
        embedder: Embedder | None = None,
        generator: Generator | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.mode = mode
        self.k = k
        self.max_memory_tokens = max_memory_tokens
        self.score_threshold = score_threshold
        self.read_only = read_only
        self.embedder = embedder
        self.generator = generator

    def save(self, value: Any, metadata: dict[str, Any] | None = None) -> None:
        """Persist a memory item (text). No-op on a read-only tier (e.g. insight)."""
        if self.read_only:
            return
        text = value if isinstance(value, str) else str(value)
        if not text:
            return
        store(
            self.db,
            content=text,
            tenant_id=self.tenant_id,
            agent_id=self.agent_id,
            mode=self.mode,
            embedder=self.embedder,
            generator=self.generator,
        )

    def search(
        self,
        query: str,
        limit: int | None = None,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve relevant memories as CrewAI-shaped result dicts (no embeddings)."""
        k = limit or self.k
        threshold = self.score_threshold if score_threshold is None else score_threshold
        result = retrieve(
            self.db,
            query=query,
            tenant_id=self.tenant_id,
            agent_id=self.agent_id,
            k=k,
            max_memory_tokens=self.max_memory_tokens,
            mode=self.mode,
            embedder=self.embedder,
            generator=self.generator,
        )
        return [
            {
                "context": hit.text,
                "score": hit.score,
                "metadata": {"source": hit.source, "agent_id": self.agent_id},
            }
            for hit in result.hits
            if hit.score >= threshold
        ][:k]

    def reset(self) -> None:
        """Right-to-be-forgotten soft-delete of this tier's memory. No-op if read-only."""
        if self.read_only:
            return
        forget(self.db, tenant_id=self.tenant_id, agent_id=self.agent_id)


@dataclass(frozen=True)
class CrewMemory:
    """The G-Memory 3-tier memory surface for a crew (§14)."""

    interaction: ArangoCrewStorage
    query: ArangoCrewStorage
    insight: ArangoCrewStorage


def crew_memory(
    db: StandardDatabase,
    *,
    tenant_id: str,
    crew_id: str,
    agent_id: str,
    mode: str = "lite",
    k: int = 5,
    embedder: Embedder | None = None,
    generator: Generator | None = None,
) -> CrewMemory:
    """Build the 3-tier crew memory for one agent (private + shared + insight)."""
    common: dict[str, Any] = {
        "tenant_id": tenant_id,
        "mode": mode,
        "k": k,
        "embedder": embedder,
        "generator": generator,
    }
    return CrewMemory(
        interaction=ArangoCrewStorage(db, agent_id=agent_id, **common),
        query=ArangoCrewStorage(db, agent_id=f"{crew_id}::query", **common),
        insight=ArangoCrewStorage(db, agent_id=f"{crew_id}::insight", read_only=True, **common),
    )
