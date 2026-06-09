"""Entity + edge writes with write-time conflict detection (DESIGN.md §5, §8 Stage 3).

Called once per *new* episode (the store path skips this on idempotent replays,
so `mention_count` stays stable). For each extracted entity:
  - cosine vs the tenant's existing entities:
      ≥ merge_threshold → treat as the same entity (bump its mention_count)
      ≥ flag_threshold  → create, but mark `needs_review` for Dream State (§13)
      otherwise         → create
  - UPSERT by natural key (tenant, name, label) so exact repeats just increment.
Then write `mentions` (memory→entity), `produced_by` (entity→episode), and
`relates_to` (entity↔entity co-occurrence) edges, all idempotently.

Conflict detection is brute-force over the tenant's entities (O(n) per turn); an
entity vector index is a later optimization.
"""

from __future__ import annotations

import math
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..embedding import Embedder
from ..models import utcnow_iso
from ..telemetry import metrics
from .extract import ExtractedEntity, Extractor, cooccurring_pairs
from .temporal import parse_explicit_time

_UPSERT_ENTITY = """
UPSERT { tenant_id: @tenant_id, name: @name, label: @label }
INSERT @doc
UPDATE { mention_count: OLD.mention_count + 1, accessed_at: @now }
IN entities
RETURN NEW._key
"""

_FETCH_EXISTING = """
FOR e IN entities
  FILTER e.tenant_id == @tenant_id
  RETURN { key: e._key, name: e.name, label: e.label, embedding: e.embedding }
"""

_INCREMENT = """
FOR e IN entities
  FILTER e._key == @key
  UPDATE e WITH { mention_count: e.mention_count + 1, accessed_at: @now } IN entities
"""


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0.0 or nb == 0.0 else dot / (na * nb)


def _best_match(
    vec: list[float], existing: list[dict[str, Any]], exclude: tuple[str, str]
) -> tuple[dict[str, Any] | None, float]:
    best: dict[str, Any] | None = None
    best_sim = -1.0
    for row in existing:
        if (row["name"], row["label"]) == exclude:
            continue
        sim = _cos(vec, row.get("embedding") or [])
        if sim > best_sim:
            best_sim, best = sim, row
    return best, best_sim


def _edge(
    db: StandardDatabase, collection: str, key: str, from_id: str, to_id: str, **extra: Any
) -> None:
    now = utcnow_iso()
    doc = {
        "_key": key,
        "_from": from_id,
        "_to": to_id,
        "ingestion_time": now,
        "valid_time": now,          # defaults to ingestion_time (§4; explicit parse → 3e)
        "valid_time_explicit": False,
        "invalid_at": None,
        "weight": 1.0,              # EWA computation deferred (§12)
        **extra,
    }
    db.collection(collection).insert(doc, overwrite_mode="ignore", silent=True)


def write_entities(
    db: StandardDatabase,
    *,
    memory_key: str,
    episode_key: str,
    content: str,
    tenant_id: str,
    agent_id: str,
    extractor: Extractor,
    embedder: Embedder,
) -> list[str]:
    """Extract, dedupe/flag, persist entities + edges. Returns resolved entity keys."""
    extracted = extractor.extract(content)
    if not extracted:
        return []

    cursor = cast(Cursor, db.aql.execute(_FETCH_EXISTING, bind_vars={"tenant_id": tenant_id}))
    existing = list(cursor)
    now = utcnow_iso()
    explicit_vt = parse_explicit_time(content)  # when the fact holds (§4)
    key_by_entity: dict[tuple[str, str], str] = {}
    key_by_name: dict[str, str] = {}
    detected = 0

    for ent in extracted:
        vec = embedder.embed(ent.name)
        match, sim = _best_match(vec, existing, exclude=(ent.name, ent.label))

        if match is not None and sim >= settings.entity_merge_threshold:
            # Semantic duplicate of a differently-named entity → merge into it.
            db.aql.execute(_INCREMENT, bind_vars={"key": match["key"], "now": now})
            key = cast(str, match["key"])
        else:
            needs_review = match is not None and sim >= settings.entity_flag_threshold
            detected += int(needs_review)
            doc = _entity_doc(
                ent, vec, tenant_id, agent_id, embedder, now, needs_review, match, explicit_vt
            )
            cursor = cast(
                Cursor,
                db.aql.execute(
                    _UPSERT_ENTITY,
                    bind_vars={
                        "tenant_id": tenant_id,
                        "name": ent.name,
                        "label": ent.label,
                        "doc": doc,
                        "now": now,
                    },
                ),
            )
            key = cast(str, next(iter(cursor)))

        key_by_entity[(ent.name, ent.label)] = key
        key_by_name.setdefault(ent.name, key)
        _edge(db, "mentions", f"{memory_key}__{key}", f"memories/{memory_key}", f"entities/{key}")
        _edge(
            db, "produced_by", f"{key}__{episode_key}",
            f"entities/{key}", f"episodes/{episode_key}",
        )

    # Typed relations (GLiREL/Haiku) win; written first so the co-occurrence pass
    # below (same edge key, overwrite_mode="ignore") only fills untyped pairs (§5).
    vt_extra: dict[str, Any] = (
        {"valid_time": explicit_vt, "valid_time_explicit": True} if explicit_vt else {}
    )
    for rel in extractor.extract_relations(content, extracted):
        ka, kb = key_by_name.get(rel.source), key_by_name.get(rel.target)
        if not ka or not kb or ka == kb:
            continue
        lo, hi = sorted((ka, kb))
        _edge(
            db, "relates_to", f"{lo}__{hi}", f"entities/{lo}", f"entities/{hi}",
            relationship=rel.relationship, **vt_extra,
        )

    for left, right in cooccurring_pairs(extracted):
        ka = key_by_entity[(left.name, left.label)]
        kb = key_by_entity[(right.name, right.label)]
        if ka == kb:
            continue
        lo, hi = sorted((ka, kb))
        _edge(
            db, "relates_to", f"{lo}__{hi}", f"entities/{lo}", f"entities/{hi}",
            relationship="associated_with", **vt_extra,
        )

    if detected:
        metrics.emit("conflict", detected=detected)

    # Preserve extraction order, de-duplicated.
    return list(dict.fromkeys(key_by_entity.values()))


def _entity_doc(
    ent: ExtractedEntity,
    vec: list[float],
    tenant_id: str,
    agent_id: str,
    embedder: Embedder,
    now: str,
    needs_review: bool,
    match: dict[str, Any] | None,
    valid_time: str | None = None,
) -> dict[str, Any]:
    return {
        "name": ent.name,
        "label": ent.label,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "embedding": vec,
        "embedding_model": embedder.model,
        "embedding_version": embedder.version,
        "mention_count": 1,
        "confidence": 1.0,
        "source": "observed",
        "summary": "",              # distilled by Dream State (§13)
        "consolidated_at": None,
        "ingestion_time": now,
        # valid_time = when the fact holds (§4): an explicit date parsed from the
        # text if present (3e), else ingestion_time.
        "valid_time": valid_time or now,
        "valid_time_explicit": valid_time is not None,
        "invalid_at": None,         # soft-deprecation marker (set by supersede, §12)
        "created_at": now,
        "accessed_at": now,
        "needs_review": needs_review,
        "conflict_with": match["key"] if needs_review and match else None,
        "schema_version": "0.1.0",
    }
