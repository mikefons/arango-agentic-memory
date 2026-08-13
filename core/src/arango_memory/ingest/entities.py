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

Candidate generation for that cosine check is by **ANN** once the tenant warms: a Faiss IVF
index on `entities.embedding` (SC-1b) lets each new entity query only its top-k nearest
existing entities, instead of full-scanning the tenant's entities every write (which made
ingestion O(N²) as a tenant filled). Below the index's training threshold it falls back to the
full scan (fine at small N). The merge/flag decision itself is unchanged.
"""

from __future__ import annotations

import math
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..embedding import Embedder
from ..embedding_cache import embed_batch_cached
from ..models import utcnow_iso
from ..schema.collections import ensure_vector_index
from ..telemetry import metrics
from ..telemetry.logging import logger
from .extract import ExtractedEntity, Extractor, cooccurring_pairs
from .temporal import parse_explicit_time

# A corroborating mention bumps mention_count, accumulates the source's
# reliability, and recomputes belief = confidence_prior × (1 − (1−base)^Σreliability) (§8/§12).
_UPSERT_ENTITY = """
UPSERT { tenant_id: @tenant_id, name: @name, label: @label }
INSERT @doc
UPDATE {
  mention_count: OLD.mention_count + 1,
  reliability_sum: NOT_NULL(OLD.reliability_sum, 0) + @rel,
  belief: OLD.confidence * (1 - POW(1 - @base, NOT_NULL(OLD.reliability_sum, 0) + @rel)),
  accessed_at: @now
}
IN entities
RETURN NEW._key
"""

_FETCH_EXISTING = """
FOR e IN entities
  FILTER e.tenant_id == @tenant_id
  RETURN { key: e._key, name: e.name, label: e.label, embedding: e.embedding }
"""

# SC-1b: top-k nearest existing entities to a query vector, via the Faiss IVF index —
# O(k) candidate generation instead of the O(N) full tenant scan above. Same shape as
# `_FETCH_EXISTING` so `_best_match` consumes either.
_NEAREST_ENTITIES = """
FOR e IN entities
  FILTER e.tenant_id == @tenant_id
  LET score = APPROX_NEAR_COSINE(e.embedding, @qvec)
  SORT score DESC
  LIMIT @topk
  RETURN { key: e._key, name: e.name, label: e.label, embedding: e.embedding }
"""

_INCREMENT = """
FOR e IN entities
  FILTER e._key == @key
  UPDATE e WITH {
    mention_count: e.mention_count + 1,
    reliability_sum: NOT_NULL(e.reliability_sum, 0) + @rel,
    belief: e.confidence * (1 - POW(1 - @base, NOT_NULL(e.reliability_sum, 0) + @rel)),
    accessed_at: @now
  } IN entities
"""

# A relates_to edge corroborated by another episode: bump the count, accumulate
# reliability, recompute belief (prior 1.0 for a relation). Typed relationship
# from a later assertion wins (last-writer). mentions/produced_by stay idempotent.
_RELATE = """
UPSERT { _key: @key }
INSERT @doc
UPDATE {
  corroboration: NOT_NULL(OLD.corroboration, 1) + 1,
  reliability_sum: NOT_NULL(OLD.reliability_sum, 0) + @rel,
  belief: 1 - POW(1 - @base, NOT_NULL(OLD.reliability_sum, 0) + @rel),
  // EWA edge weight (§12): blend a fresh 1.0 with the time-decayed prior, so
  // recently/frequently confirmed relations weigh more than stale ones.
  weight: @w_alpha + (1 - @w_alpha) * NOT_NULL(OLD.weight, @w_alpha)
          * EXP(@w_neg_lam * DATE_DIFF(NOT_NULL(OLD.last_seen, @now), @now, "s") / 86400.0),
  relationship: @relationship,
  last_seen: @now
}
IN relates_to
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


def _relate(
    db: StandardDatabase,
    *,
    key: str,
    from_id: str,
    to_id: str,
    relationship: str,
    rel: float,
    base: float,
    now: str,
    valid_time: str | None,
) -> None:
    """Corroborate (UPSERT-increment) one `relates_to` edge for this episode (§5/§12)."""
    doc = {
        "_key": key,
        "_from": from_id,
        "_to": to_id,
        "relationship": relationship,
        "corroboration": 1,
        "reliability_sum": rel,
        "belief": 1 - (1 - base) ** rel,
        "ingestion_time": now,
        "valid_time": valid_time or now,
        "valid_time_explicit": valid_time is not None,
        "invalid_at": None,
        "weight": settings.weight_ewa_alpha,  # EWA seed (§12)
        "last_seen": now,
    }
    bind: dict[str, Any] = {
        "key": key, "doc": doc, "relationship": relationship,
        "rel": rel, "base": base, "now": now,
        "w_alpha": settings.weight_ewa_alpha,
        "w_neg_lam": -settings.weight_lambda,
    }
    db.aql.execute(_RELATE, bind_vars=bind)


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
    source_reliability: float = 1.0,
) -> list[str]:
    """Extract, dedupe/flag, persist entities + edges. Returns resolved entity keys.

    `source_reliability` (0..1) weights how much this episode corroborates each
    fact: it accumulates into `reliability_sum` and drives `belief` (§8/§12).
    """
    extracted = extractor.extract(content)
    if not extracted:
        return []

    # SC-1b: use the ANN index (top-k nearest) once the `entities` collection is warm enough
    # to train it; below that, scan the tenant's entities once (cached, lazy) as before.
    use_ann = ensure_vector_index(
        db,
        dimensions=embedder.dimensions,
        n_lists=settings.entity_vector_n_lists,
        train_factor=settings.entity_vector_train_factor,
        collection="entities",
    )
    scan_cache: list[dict[str, Any]] | None = None

    def _candidates(qvec: list[float]) -> list[dict[str, Any]]:
        nonlocal scan_cache
        if use_ann and qvec:
            try:
                ann_bind: dict[str, Any] = {
                    "tenant_id": tenant_id, "qvec": qvec,
                    "topk": settings.entity_resolution_top_k,
                }
                cur = cast(Cursor, db.aql.execute(_NEAREST_ENTITIES, bind_vars=ann_bind))
                return list(cur)
            except Exception as exc:  # noqa: BLE001 — ANN fault → fall back to the scan, never break ingest
                logger.warning("entity ANN resolution failed; scanning",
                               extra={"reason": type(exc).__name__, "detail": str(exc)})
        if scan_cache is None:
            cur = cast(Cursor, db.aql.execute(_FETCH_EXISTING, bind_vars={"tenant_id": tenant_id}))
            scan_cache = list(cur)
        return scan_cache

    now = utcnow_iso()
    base = settings.corroboration_base
    rel = source_reliability
    explicit_vt = parse_explicit_time(content)  # when the fact holds (§4)
    key_by_entity: dict[tuple[str, str], str] = {}
    key_by_name: dict[str, str] = {}
    detected = 0

    # Embed all distinct entity names up front — one provider call for the misses
    # (§16 batch embedding), keeping per-name cache semantics intact.
    vec_by_name = embed_batch_cached(
        embedder, [ent.name for ent in extracted], tenant_id=tenant_id
    )

    for ent in extracted:
        vec = vec_by_name[ent.name]
        match, sim = _best_match(vec, _candidates(vec), exclude=(ent.name, ent.label))

        if match is not None and sim >= settings.entity_merge_threshold:
            # Semantic duplicate of a differently-named entity → merge into it.
            inc_bind: dict[str, Any] = {"key": match["key"], "now": now, "rel": rel, "base": base}
            db.aql.execute(_INCREMENT, bind_vars=inc_bind)
            key = cast(str, match["key"])
        else:
            needs_review = match is not None and sim >= settings.entity_flag_threshold
            detected += int(needs_review)
            doc = _entity_doc(
                ent, vec, tenant_id, agent_id, embedder, now, needs_review, match, explicit_vt,
                rel, base,
            )
            up_bind: dict[str, Any] = {
                "tenant_id": tenant_id,
                "name": ent.name,
                "label": ent.label,
                "doc": doc,
                "now": now,
                "rel": rel,
                "base": base,
            }
            cursor = cast(Cursor, db.aql.execute(_UPSERT_ENTITY, bind_vars=up_bind))
            key = cast(str, next(iter(cursor)))

        key_by_entity[(ent.name, ent.label)] = key
        key_by_name.setdefault(ent.name, key)
        _edge(db, "mentions", f"{memory_key}__{key}", f"memories/{memory_key}", f"entities/{key}")
        _edge(
            db, "produced_by", f"{key}__{episode_key}",
            f"entities/{key}", f"episodes/{episode_key}",
        )

    # One relation per entity pair (typed relationship wins over co-occurrence),
    # corroborated exactly once per episode so the count tracks independent episodes.
    pairs: dict[tuple[str, str], str] = {}
    for relation in extractor.extract_relations(content, extracted):
        ka, kb = key_by_name.get(relation.source), key_by_name.get(relation.target)
        if not ka or not kb or ka == kb:
            continue
        lo, hi = sorted((ka, kb))
        pairs[(lo, hi)] = relation.relationship
    # IN-3: typed relations are already in `pairs`; bound the co-occurrence backfill so a dense
    # turn can't flood the graph with low-signal edges (setdefault keeps typed relations).
    for left, right in cooccurring_pairs(extracted, max_pairs=settings.graph_max_pairs_per_turn):
        ka = key_by_entity[(left.name, left.label)]
        kb = key_by_entity[(right.name, right.label)]
        if ka == kb:
            continue
        lo, hi = sorted((ka, kb))
        pairs.setdefault((lo, hi), "associated_with")  # don't downgrade a typed relation
    for (lo, hi), relationship in pairs.items():
        _relate(
            db, key=f"{lo}__{hi}", from_id=f"entities/{lo}", to_id=f"entities/{hi}",
            relationship=relationship, rel=rel, base=base, now=now, valid_time=explicit_vt,
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
    reliability: float = 1.0,
    base: float = 0.5,
) -> dict[str, Any]:
    confidence = 1.0
    return {
        "name": ent.name,
        "label": ent.label,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "embedding": vec,
        "embedding_model": embedder.model,
        "embedding_version": embedder.version,
        "mention_count": 1,
        "confidence": confidence,
        # belief = evidential confidence from corroboration × source reliability (§8/§12);
        # `confidence` stays the source prior (observed 1.0 / seed 0.6).
        "reliability_sum": reliability,
        "belief": confidence * (1 - (1 - base) ** reliability),
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
