"""Minimal ingestion for the Step 0 walking skeleton (DESIGN.md §8).

Writes a WORM episode and an episodic memory, both keyed by an idempotency
hash so retries cannot duplicate. PII redaction, multi-stage extraction,
prospective indexing, and the durable write queue are added in later steps.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..embedding import Embedder, get_embedder
from ..embedding_cache import embed_cached
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
    source_reliability: float = 1.0,
    memory_type: str = "episodic",
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
            source_reliability=source_reliability,
            memory_type=memory_type,
        )
    metrics.emit("write", duration_ms=(time.perf_counter() - started) * 1000.0)
    return result


# SCM cap (§14): keep at most `working_capacity` active working memories per
# (tenant, agent, session); promote the oldest overflow to episodic (clearing the
# TTL) — "overflow compresses oldest to episodic".
_ENFORCE_CAPACITY = """
FOR m IN memories
  FILTER m.type == "working" AND m.invalid_at == null
     AND m.tenant_id == @tenant_id AND m.agent_id == @agent_id
     AND m.session_id == @session_id
  SORT m.created_at DESC
  LIMIT @capacity, 2147483647
  UPDATE m WITH { type: "episodic", expires_at: null } IN memories
  RETURN 1
"""


def _enforce_working_capacity(
    db: StandardDatabase, *, tenant_id: str, agent_id: str, session_id: str | None
) -> int:
    bind: dict[str, Any] = {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "session_id": session_id,
        "capacity": settings.working_capacity,
    }
    cursor = cast(Cursor, db.aql.execute(_ENFORCE_CAPACITY, bind_vars=bind))
    return len(list(cursor))


# Promote the whole working buffer for a session to episodic (topic-shift flush, §13).
_FLUSH_WORKING = """
FOR m IN memories
  FILTER m.type == "working" AND m.invalid_at == null
     AND m.tenant_id == @tenant_id AND m.agent_id == @agent_id
     AND m.session_id == @session_id
  UPDATE m WITH { type: "episodic", expires_at: null } IN memories
  RETURN 1
"""


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _session_key(tenant_id: str, agent_id: str, session_id: str) -> str:
    raw = f"{tenant_id}\x1f{agent_id}\x1f{session_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _track_session_topic(
    db: StandardDatabase,
    *,
    tenant_id: str,
    agent_id: str,
    session_id: str,
    turn_vec: list[float],
    now: str,
) -> bool:
    """Update the session's running topic; on a topic shift, flush working → episodic.

    Returns True iff a topic shift was detected. The "consolidation check" is
    signal-only (a `topic_shift` metric + `consolidation_due` flag); Dream State
    stays a separate scheduled pass so the write path never blocks on an LLM.
    """
    sessions = db.collection("sessions")
    key = _session_key(tenant_id, agent_id, session_id)
    prior = cast("dict[str, Any] | None", sessions.get(key))

    if prior is None:  # first turn → seed the topic, no shift
        sessions.insert(
            {
                "_key": key, "tenant_id": tenant_id, "agent_id": agent_id,
                "session_id": session_id, "topic_embedding": turn_vec,
                "started_at": now, "updated_at": now, "consolidation_due": False,
            },
            overwrite_mode="ignore", silent=True,
        )
        return False

    shifted = _cosine(turn_vec, prior["topic_embedding"]) < settings.topic_shift_threshold
    if shifted:
        flushed = len(list(cast(Cursor, db.aql.execute(
            _FLUSH_WORKING,
            bind_vars={"tenant_id": tenant_id, "agent_id": agent_id, "session_id": session_id},
        ))))
        sessions.update(
            {"_key": key, "topic_embedding": turn_vec, "updated_at": now, "consolidation_due": True}
        )
        metrics.emit("topic_shift", tenant_id=tenant_id, flushed=flushed)
    else:
        alpha = settings.topic_ewa_alpha
        blended = [
            alpha * t + (1.0 - alpha) * p
            for t, p in zip(turn_vec, prior["topic_embedding"], strict=False)
        ]
        sessions.update({"_key": key, "topic_embedding": blended, "updated_at": now})
    return shifted


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
    source_reliability: float = 1.0,
    memory_type: str = "episodic",
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
        "source_reliability": source_reliability,
        "ingested_at": now,
    }
    db.collection("episodes").insert(episode, overwrite_mode="ignore", silent=True)

    # Working memory (§5/§14): a session-scoped scratch tier that auto-expires via a
    # TTL index. Episodic memories carry expires_at=null (the TTL index ignores them).
    is_working = memory_type == "working"
    expires_at = (
        (datetime.fromisoformat(now) + timedelta(seconds=settings.working_session_ttl_seconds))
        .isoformat()
        if is_working
        else None
    )

    turn_vec = embed_cached(emb, content, tenant_id=tenant_id)
    # GAM semantic boundary (§13): track the session's running topic before writing
    # this turn's memory, so a detected shift flushes the *prior* working buffer.
    if is_new and session_id is not None:
        _track_session_topic(
            db, tenant_id=tenant_id, agent_id=agent_id, session_id=session_id,
            turn_vec=turn_vec, now=now,
        )

    mem_key = f"{key}-mem"
    memory = {
        "_key": mem_key,
        "idempotency_key": mem_key,
        "text": content,
        "type": "working" if is_working else "episodic",
        "strength": 1.0,
        "access_count": 1,
        "created_at": now,
        "accessed_at": now,
        "invalid_at": None,
        "expires_at": expires_at,
        "session_id": session_id,
        "source_episode_id": key,
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "schema_version": "0.1.0",
        "embedding": turn_vec,
        "embedding_model": emb.model,
        "embedding_version": emb.version,
        "prospective_queries": prospective,
    }
    db.collection("memories").insert(memory, overwrite_mode="ignore", silent=True)

    if is_new and is_working:
        _enforce_working_capacity(db, tenant_id=tenant_id, agent_id=agent_id, session_id=session_id)

    entity_ids: list[str] = []
    # Working memory is ephemeral scratch — it never mints durable semantic entities.
    if is_new and not is_working:
        entity_ids = write_entities(
            db,
            memory_key=mem_key,
            episode_key=key,
            content=content,
            tenant_id=tenant_id,
            agent_id=agent_id,
            extractor=extractor or get_extractor(),
            embedder=emb,
            source_reliability=source_reliability,
        )

    return StoreResult(episode_id=key, memory_ids=[mem_key], entity_ids=entity_ids)
