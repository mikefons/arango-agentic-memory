"""Minimal ingestion for the Step 0 walking skeleton (DESIGN.md §8).

Writes a WORM episode and an episodic memory, both keyed by an idempotency
hash so retries cannot duplicate. PII redaction, multi-stage extraction,
prospective indexing, and the durable write queue are added in later steps.
"""

from __future__ import annotations

from dataclasses import dataclass

from arango.database import StandardDatabase

from ..models import idempotency_key, utcnow_iso


@dataclass(frozen=True)
class StoreResult:
    episode_id: str
    memory_ids: list[str]
    entity_ids: list[str]


def store(
    db: StandardDatabase,
    *,
    content: str,
    tenant_id: str,
    agent_id: str,
    session_id: str | None = None,
    turn_index: int = 0,
) -> StoreResult:
    """Persist one turn as an episode + episodic memory. Idempotent."""
    now = utcnow_iso()
    key = idempotency_key(
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=session_id,
        content=content,
        turn_index=turn_index,
    )

    episode = {
        "_key": key,
        "idempotency_key": key,
        "content": content,
        "source_type": "interaction",
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "ingested_at": now,
    }
    db.collection("episodes").insert(episode, overwrite_mode="ignore", silent=True)

    mem_key = f"{key}-mem"
    memory = {
        "_key": mem_key,
        "idempotency_key": mem_key,
        "text": content,
        "type": "episodic",
        "strength": 1.0,
        "created_at": now,
        "accessed_at": now,
        "invalid_at": None,
        "source_episode_id": key,
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "schema_version": "0.1.0",
    }
    db.collection("memories").insert(memory, overwrite_mode="ignore", silent=True)

    return StoreResult(episode_id=key, memory_ids=[mem_key], entity_ids=[])
