"""Minimal ingestion for the Step 0 walking skeleton (DESIGN.md §8).

Writes a WORM episode and an episodic memory, both keyed by an idempotency
hash so retries cannot duplicate. PII redaction, multi-stage extraction,
prospective indexing, and the durable write queue are added in later steps.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from arango.database import StandardDatabase

from ..config import settings
from ..embedding import Embedder, get_embedder
from ..generation import Generator, get_generator
from ..models import idempotency_key, utcnow_iso
from ..security.redact import redact
from ..telemetry import metrics, span
from .entities import write_entities
from .extract import Extractor, get_extractor
from .prospective import generate_prospective


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
    embedder: Embedder | None = None,
    extractor: Extractor | None = None,
    generator: Generator | None = None,
    mode: str = "lite",
    message_type: str | None = None,
) -> StoreResult:
    """Instrumented write (DESIGN.md §18): `memory.write` span + `write` metric.

    `message_type` (optional) tags the episode with the originating chat role
    (e.g. "human"/"ai") for adapters that reconstruct a transcript; it never
    affects redaction, hashing, or the embedded text.

    Exceptions propagate to the durable worker (retry/dead-letter, §15).
    """
    started = time.perf_counter()
    with span("memory.write", tenant_id=tenant_id):
        result = _store_impl(
            db,
            content=content,
            tenant_id=tenant_id,
            agent_id=agent_id,
            session_id=session_id,
            turn_index=turn_index,
            embedder=embedder,
            extractor=extractor,
            generator=generator,
            mode=mode,
            message_type=message_type,
        )
    metrics.emit("write", duration_ms=(time.perf_counter() - started) * 1000.0)
    return result


def _store_impl(
    db: StandardDatabase,
    *,
    content: str,
    tenant_id: str,
    agent_id: str,
    session_id: str | None = None,
    turn_index: int = 0,
    embedder: Embedder | None = None,
    extractor: Extractor | None = None,
    generator: Generator | None = None,
    mode: str = "lite",
    message_type: str | None = None,
) -> StoreResult:
    """Persist one turn as an episode + episodic memory, with extracted entities.

    The memory carries an embedding for vector retrieval (DESIGN.md §5, §9).
    Entity/edge extraction and prospective indexing (full mode) run only on the
    first store of a turn so idempotent replays don't double-count (DESIGN.md §8).
    """
    emb = embedder or get_embedder()
    # Redact PII before anything is persisted or hashed — the original is never
    # stored (§17). Everything below operates on the redacted content.
    if settings.redact_pii:
        content = redact(
            content,
            mode=mode,
            generator=(generator or get_generator()) if mode == "full" else None,
        )
    now = utcnow_iso()
    key = idempotency_key(
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=session_id,
        content=content,
        turn_index=turn_index,
    )
    is_new = not db.collection("episodes").has(key)

    prospective: list[str] = []
    if is_new and mode == "full":
        prospective = generate_prospective(content, generator or get_generator())

    episode = {
        "_key": key,
        "idempotency_key": key,
        "content": content,
        "source_type": "interaction",
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "message_type": message_type,
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
        "access_count": 1,
        "created_at": now,
        "accessed_at": now,
        "invalid_at": None,
        "source_episode_id": key,
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "schema_version": "0.1.0",
        "embedding": emb.embed(content),
        "embedding_model": emb.model,
        "embedding_version": emb.version,
        "prospective_queries": prospective,
    }
    db.collection("memories").insert(memory, overwrite_mode="ignore", silent=True)

    entity_ids: list[str] = []
    if is_new:
        entity_ids = write_entities(
            db,
            memory_key=mem_key,
            episode_key=key,
            content=content,
            tenant_id=tenant_id,
            agent_id=agent_id,
            extractor=extractor or get_extractor(),
            embedder=emb,
        )

    return StoreResult(episode_id=key, memory_ids=[mem_key], entity_ids=entity_ids)
