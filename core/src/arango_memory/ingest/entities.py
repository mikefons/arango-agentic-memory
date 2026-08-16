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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
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
from .extract import ExtractedEntity, ExtractedRelation, Extractor, cooccurring_pairs
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

# IN-6: the union of top-k nearest existing entities across a whole batch of query vectors, in
# ONE round trip (vs one `_NEAREST_ENTITIES` call per entity). The union is a superset of each
# vector's own top-k, so the per-entity best match is unchanged; DISTINCT dedups the overlap.
_NEAREST_ENTITIES_BATCH = """
FOR qvec IN @qvecs
  FOR e IN entities
    FILTER e.tenant_id == @tenant_id
    LET score = APPROX_NEAR_COSINE(e.embedding, qvec)
    SORT score DESC
    LIMIT @topk
    RETURN DISTINCT { key: e._key, name: e.name, label: e.label, embedding: e.embedding }
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


def _best_match_many(
    qvecs: list[list[float]],
    pool: list[dict[str, Any]],
    *,
    exclude: list[tuple[str, str]],
) -> list[tuple[dict[str, Any] | None, float]]:
    """Vectorized `_best_match` (IN-6): the best cosine match in `pool` for every query vector
    at once, excluding each query's own (name, label). One numpy matmul replaces the N × M
    Python cosine loop that made graph-on ingest O(cardinality²). Result order matches `qvecs`;
    an empty pool or an all-excluded row yields (None, -1.0) — identical to `_best_match`."""
    import numpy as np

    n = len(qvecs)
    if n == 0:
        return []
    if not pool:
        return [(None, -1.0)] * n

    def _unit(mat: Any) -> Any:  # L2-normalize rows; a zero row stays zero → cosine 0.0 (as _cos)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        return np.divide(mat, norms, out=np.zeros_like(mat), where=norms != 0)

    dim = len(qvecs[0])
    q = _unit(np.asarray(qvecs, dtype=np.float64))
    p = _unit(np.asarray([row.get("embedding") or [0.0] * dim for row in pool], dtype=np.float64))
    sims = q @ p.T  # (N, M) cosine

    # Exclude a query's own (name, label). Map tuples → dense ids for exact (collision-free)
    # equality, then mask those cells to -inf so they can't win.
    ids: dict[tuple[str, str], int] = {}
    q_ids = np.asarray([ids.setdefault(nl, len(ids)) for nl in exclude])
    p_ids = np.asarray([ids.setdefault((r["name"], r["label"]), len(ids)) for r in pool])
    sims[q_ids[:, None] == p_ids[None, :]] = -np.inf

    best_j = np.argmax(sims, axis=1)
    out: list[tuple[dict[str, Any] | None, float]] = []
    for i in range(n):
        s = float(sims[i, best_j[i]])
        out.append((None, -1.0) if s == float("-inf") else (pool[int(best_j[i])], s))
    return out


def _resolution_pool(
    db: StandardDatabase, tenant_id: str, qvecs: list[list[float]], *, use_ann: bool
) -> list[dict[str, Any]]:
    """Candidate existing-entity rows to resolve this batch against, in ONE round trip (IN-6).
    ANN warm → the union of every query vector's top-k nearest (a superset of the old per-entity
    ANN candidates, so best-match is unchanged); otherwise a single full tenant scan. Any ANN
    fault falls back to the scan — never breaks ingest."""
    if use_ann and qvecs:
        try:
            bind: dict[str, Any] = {
                "tenant_id": tenant_id, "qvecs": qvecs,
                "topk": settings.entity_resolution_top_k,
            }
            return list(cast(Cursor, db.aql.execute(_NEAREST_ENTITIES_BATCH, bind_vars=bind)))
        except Exception as exc:  # noqa: BLE001 — ANN fault → scan, never break ingest
            logger.warning("entity ANN resolution failed; scanning",
                           extra={"reason": type(exc).__name__, "detail": str(exc)})
    return list(cast(Cursor, db.aql.execute(_FETCH_EXISTING, bind_vars={"tenant_id": tenant_id})))


def _edge_doc(key: str, from_id: str, to_id: str, now: str, **extra: Any) -> dict[str, Any]:
    return {
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


def _edge(
    db: StandardDatabase, collection: str, key: str, from_id: str, to_id: str, **extra: Any
) -> None:
    doc = _edge_doc(key, from_id, to_id, utcnow_iso(), **extra)
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


# ── IN-2: batched graph pass — reflect a batch of memories in a handful of round trips ──

@dataclass(frozen=True)
class GraphMemory:
    """One recorded memory to reflect into the graph (IN-2 batched pass)."""

    memory_key: str
    episode_key: str
    content: str
    source_reliability: float = 1.0


_PerMem = tuple[GraphMemory, list[ExtractedEntity], list[ExtractedRelation], "str | None"]


@dataclass
class _EntityAccum:
    ent: ExtractedEntity
    vec: list[float]
    valid_time: str | None
    count: int = 0
    rel_sum: float = 0.0
    mem_refs: list[tuple[str, str]] = field(default_factory=list)  # (memory_key, episode_key)


# Bulk entity upsert: INSERT carries the batch totals (mention_count/reliability_sum), UPDATE
# ADDS them to an existing entity — identical to N sequential single upserts (belief is a
# function of the reliability *sum*, §8/§12).
_BULK_UPSERT_ENTITY = """
FOR row IN @rows
  UPSERT { tenant_id: @tenant_id, name: row.name, label: row.label }
  INSERT row.doc
  UPDATE {
    mention_count: OLD.mention_count + row.count,
    reliability_sum: NOT_NULL(OLD.reliability_sum, 0) + row.rel_sum,
    belief: OLD.confidence * (1 - POW(1 - @base, NOT_NULL(OLD.reliability_sum, 0) + row.rel_sum)),
    accessed_at: @now
  }
  IN entities
  RETURN { name: row.name, label: row.label, key: NEW._key }
"""

# Bulk increment (semantic-merge targets): add each batch total to an existing entity by key.
_BULK_INCREMENT = """
FOR row IN @rows
  FOR e IN entities
    FILTER e._key == row.key
    UPDATE e WITH {
      mention_count: e.mention_count + row.count,
      reliability_sum: NOT_NULL(e.reliability_sum, 0) + row.rel_sum,
      belief: e.confidence * (1 - POW(1 - @base, NOT_NULL(e.reliability_sum, 0) + row.rel_sum)),
      accessed_at: @now
    } IN entities
"""

# Bulk relates_to upsert: same shape as _RELATE, but corroboration/reliability accumulate the
# batch totals. The EWA weight is folded once (Δt≈0 within a batch — a recency heuristic).
_BULK_RELATE = """
FOR row IN @rows
  UPSERT { _key: row.key }
  INSERT row.doc
  UPDATE {
    corroboration: NOT_NULL(OLD.corroboration, 1) + row.corr,
    reliability_sum: NOT_NULL(OLD.reliability_sum, 0) + row.rel_sum,
    belief: 1 - POW(1 - @base, NOT_NULL(OLD.reliability_sum, 0) + row.rel_sum),
    weight: @w_alpha + (1 - @w_alpha) * NOT_NULL(OLD.weight, @w_alpha)
            * EXP(@w_neg_lam * DATE_DIFF(NOT_NULL(OLD.last_seen, @now), @now, "s") / 86400.0),
    relationship: row.relationship,
    last_seen: @now
  }
  IN relates_to
"""


def write_entities_many(
    db: StandardDatabase,
    memories: list[GraphMemory],
    *,
    tenant_id: str,
    agent_id: str,
    extractor: Extractor,
    embedder: Embedder,
) -> dict[str, list[str]]:
    """Reflect a *batch* of recorded memories into the entity graph in a handful of round trips
    instead of ~4E+pairs per memory. Extract per memory, resolve distinct entities once, then
    **bulk**-upsert entities and **bulk**-insert edges. Belief/corroboration fold by sum, so the
    result equals calling `write_entities` per memory (DESIGN §8). Returns memory_key → keys.

    Two documented, benign differences from the sequential path: (1) intra-batch *semantic* dedup
    of two differently-named-but-similar NEW entities is left to consolidation — exact-name repeats
    still merge via the upsert key; (2) the EWA edge weight (a recency heuristic, not a correctness
    invariant) folds once per batch (Δt≈0)."""
    if not memories:
        return {}
    now = utcnow_iso()
    base = settings.corroboration_base
    result: dict[str, list[str]] = {mem.memory_key: [] for mem in memories}

    # 1. extract per memory. The model calls are inherent, but they're independent per memory,
    #    so run them under a bounded thread pool (IN-7) — the sequential loop was the graph-on
    #    wall for an I/O-bound extractor (haiku: one LLM call per turn, ~O(turns) round trips).
    #    `map` preserves order, so the downstream accumulation is byte-identical to the loop.
    def _extract_one(mem: GraphMemory) -> _PerMem:
        ents = extractor.extract(mem.content)
        return (mem, ents, extractor.extract_relations(mem.content, ents),
                parse_explicit_time(mem.content))

    workers = min(settings.extraction_concurrency, len(memories))
    if workers <= 1:
        per_mem = [_extract_one(mem) for mem in memories]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            per_mem = list(executor.map(_extract_one, memories))

    names = [e.name for _, ents, _, _ in per_mem for e in ents]
    if not names:
        return result
    vec_by_name = embed_batch_cached(embedder, names, tenant_id=tenant_id)

    # 2. accumulate per distinct (name, label) across the batch.
    accum: dict[tuple[str, str], _EntityAccum] = {}
    for mem, ents, _rels, vt in per_mem:
        for ent in {(e.name, e.label): e for e in ents}.values():  # per-memory dedup
            nl = (ent.name, ent.label)
            a = accum.get(nl)
            if a is None:
                a = accum[nl] = _EntityAccum(ent=ent, vec=vec_by_name[ent.name], valid_time=vt)
            a.count += 1
            a.rel_sum += mem.source_reliability
            a.mem_refs.append((mem.memory_key, mem.episode_key))

    # 3. resolve distinct entities against existing DB entities — one candidate-pool fetch +
    #    one vectorized (numpy) match, instead of a lookup + a Python cosine per entity (IN-6).
    #    The per-entity ANN query / O(cardinality²) scan loop was the graph-on ingest wall a
    #    real (high-cardinality) extractor exposed; folding it into a batch keeps it ~O(N·M)
    #    matmul in C. Belief/corroboration still fold by sum, so the result is unchanged.
    use_ann = ensure_vector_index(
        db, dimensions=embedder.dimensions, n_lists=settings.entity_vector_n_lists,
        train_factor=settings.entity_vector_train_factor, collection="entities",
    )
    nls = list(accum.keys())
    qvecs = [accum[nl].vec for nl in nls]
    pool = _resolution_pool(db, tenant_id, qvecs, use_ann=use_ann)
    matches = _best_match_many(qvecs, pool, exclude=nls)

    key_by_nl: dict[tuple[str, str], str] = {}
    increments: dict[str, list[float]] = {}  # existing key -> [count, rel_sum]
    own: dict[tuple[str, str], tuple[_EntityAccum, dict[str, Any] | None, bool]] = {}
    detected = 0
    for nl, (match, sim) in zip(nls, matches, strict=True):
        a = accum[nl]
        if match is not None and sim >= settings.entity_merge_threshold:
            key_by_nl[nl] = cast(str, match["key"])
            inc = increments.setdefault(cast(str, match["key"]), [0.0, 0.0])
            inc[0] += a.count
            inc[1] += a.rel_sum
        else:
            needs_review = match is not None and sim >= settings.entity_flag_threshold
            detected += int(needs_review)
            own[nl] = (a, match, needs_review)

    # 4. bulk increment semantic-merge targets.
    if increments:
        inc_rows = [{"key": k, "count": c, "rel_sum": r} for k, (c, r) in increments.items()]
        inc_bind: dict[str, Any] = {"rows": inc_rows, "now": now, "base": base}
        db.aql.execute(_BULK_INCREMENT, bind_vars=inc_bind)

    # 5. bulk upsert own entities → resolved keys.
    if own:
        up_rows: list[dict[str, Any]] = []
        for (name, label), (a, match, needs_review) in own.items():
            doc = _entity_doc(a.ent, a.vec, tenant_id, agent_id, embedder, now,
                              needs_review, match, a.valid_time, a.rel_sum, base)
            doc["mention_count"] = a.count  # INSERT carries the batch total (UPDATE adds it)
            up_rows.append({"name": name, "label": label, "doc": doc,
                            "count": a.count, "rel_sum": a.rel_sum})
        up_bind: dict[str, Any] = {
            "rows": up_rows, "tenant_id": tenant_id, "now": now, "base": base}
        cur = cast(Cursor, db.aql.execute(_BULK_UPSERT_ENTITY, bind_vars=up_bind))
        for row in cur:
            key_by_nl[(row["name"], row["label"])] = row["key"]

    name_to_key: dict[str, str] = {}
    for (name, _label), key in key_by_nl.items():
        name_to_key.setdefault(name, key)

    # 6. bulk-insert mention + produced_by edges.
    mention_edges: list[dict[str, Any]] = []
    produced_edges: list[dict[str, Any]] = []
    for nl, a in accum.items():
        key = key_by_nl[nl]
        for mkey, ekey in a.mem_refs:
            mention_edges.append(_edge_doc(
                f"{mkey}__{key}", f"memories/{mkey}", f"entities/{key}", now))
            produced_edges.append(_edge_doc(
                f"{key}__{ekey}", f"entities/{key}", f"episodes/{ekey}", now))
            result[mkey].append(key)
    if mention_edges:
        db.collection("mentions").insert_many(mention_edges, overwrite_mode="ignore", silent=True)
    if produced_edges:
        db.collection("produced_by").insert_many(
            produced_edges, overwrite_mode="ignore", silent=True)

    # 7. aggregate relates_to across the batch (typed wins; co-occurrence capped per memory).
    pair_accum: dict[tuple[str, str], dict[str, Any]] = {}
    for mem, ents, rels, vt in per_mem:
        mpairs: dict[tuple[str, str], str] = {}
        for r in rels:
            ka, kb = name_to_key.get(r.source), name_to_key.get(r.target)
            if not ka or not kb or ka == kb:
                continue
            lo, hi = sorted((ka, kb))
            mpairs[(lo, hi)] = r.relationship
        for left, right in cooccurring_pairs(ents, max_pairs=settings.graph_max_pairs_per_turn):
            ka = key_by_nl.get((left.name, left.label))
            kb = key_by_nl.get((right.name, right.label))
            if not ka or not kb or ka == kb:
                continue
            lo, hi = sorted((ka, kb))
            mpairs.setdefault((lo, hi), "associated_with")
        for pair, relationship in mpairs.items():
            pa = pair_accum.get(pair)
            if pa is None:
                pa = pair_accum[pair] = {"corr": 0, "rel_sum": 0.0, "vt": vt}
            pa["corr"] += 1
            pa["rel_sum"] += mem.source_reliability
            pa["relationship"] = relationship  # last assertion wins (mirrors the sequential path)
    if pair_accum:
        rel_rows: list[dict[str, Any]] = []
        for (lo, hi), pa in pair_accum.items():
            vt = pa["vt"]
            doc = {
                "_key": f"{lo}__{hi}", "_from": f"entities/{lo}", "_to": f"entities/{hi}",
                "relationship": pa["relationship"], "corroboration": pa["corr"],
                "reliability_sum": pa["rel_sum"], "belief": 1 - (1 - base) ** pa["rel_sum"],
                "ingestion_time": now, "valid_time": vt or now,
                "valid_time_explicit": vt is not None, "invalid_at": None,
                "weight": settings.weight_ewa_alpha, "last_seen": now,
            }
            rel_rows.append({"key": f"{lo}__{hi}", "doc": doc, "corr": pa["corr"],
                             "rel_sum": pa["rel_sum"], "relationship": pa["relationship"]})
        rel_bind: dict[str, Any] = {
            "rows": rel_rows, "base": base, "now": now,
            "w_alpha": settings.weight_ewa_alpha, "w_neg_lam": -settings.weight_lambda}
        db.aql.execute(_BULK_RELATE, bind_vars=rel_bind)

    if detected:
        metrics.emit("conflict", detected=detected)
    return {mkey: list(dict.fromkeys(keys)) for mkey, keys in result.items()}
