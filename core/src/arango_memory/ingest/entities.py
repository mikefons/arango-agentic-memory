"""Entity + edge writes with write-time conflict detection (DESIGN.md §5, §8 Stage 3).

Called once per *new* episode (the store path skips this on idempotent replays,
so `mention_count` stays stable). For each extracted entity:
  - cosine vs the tenant's existing entities:
      ≥ merge_threshold → treat as the same entity (bump its mention_count)
      ≥ flag_threshold  → create, but mark `needs_review` for Dream State (§13)
      otherwise         → create
  - One entity per tenant and name (`name_key`: type and legal suffix ignored), so a
    repeated name just increments.
Then write `mentions` (memory→entity), `produced_by` (entity→episode), and
`relates_to` (entity↔entity co-occurrence) edges, all idempotently.

Candidate generation for that cosine check is by **ANN** once the tenant warms: a Faiss IVF
index on `entities.embedding` (SC-1b) lets each new entity query only its top-k nearest
existing entities, instead of full-scanning the tenant's entities every write (which made
ingestion O(N²) as a tenant filled). Below the index's training threshold it falls back to the
full scan (fine at small N). The merge/flag decision itself is unchanged.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..embedding import Embedder
from ..embedding_cache import embed_batch_cached
from ..models import utcnow_iso
from ..schema.collections import forget_vector_state, vector_index_ready
from ..telemetry import StageTimer, metrics
from ..telemetry.logging import logger
from .conflicts import retry_on_conflict
from .extract import (
    ExtractedEntity,
    ExtractedRelation,
    Extractor,
    cooccurring_pairs,
    is_io_bound,
    strip_possessive,
)
from .temporal import parse_explicit_time
from .vector_cache import Entry, entity_vectors

# A corroborating mention bumps mention_count, accumulates the source's
# reliability, and recomputes belief = confidence_prior × (1 − (1−base)^Σreliability) (§8/§12).
# A new entity's key comes from its name (`_entity_key`), so two writers that meet the same
# new name at once collide on it and the loser, retried, corroborates the winner's.
_UPSERT_ENTITY = """
UPSERT { _key: @key }
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
  RETURN { key: e._key, name: e.name, label: e.label, embedding: e.embedding,
           invalid: e.invalid_at != null }
"""

# `_FETCH_EXISTING` for a caller that holds some embeddings already (`vector_cache`): @known maps
# a stamp to the keys cached under it, and only the others' embeddings come back.
_FETCH_UNCACHED = """
FOR e IN entities
  FILTER e.tenant_id == @tenant_id
  // ingestion_time: a purged and re-created entity reuses its key but is a new embedding
  LET stamp = CONCAT_SEPARATOR(
    "|", e.embedding_model, e.embedding_version, e.reembedded_at, e.ingestion_time)
  LET cached = HAS(NOT_NULL(@known[stamp], {}), e._key)
  RETURN { key: e._key, name: e.name, label: e.label, stamp: stamp, cached: cached,
           embedding: cached ? null : e.embedding, invalid: e.invalid_at != null }
"""
_SCAN_BATCH_SIZE = 10_000  # rows per cursor page: cached rows are small, so fewer round trips

# SC-1b: top-k nearest existing entities to a query vector, via the Faiss IVF index —
# O(k) candidate generation instead of the O(N) full tenant scan above. Same shape as
# `_FETCH_EXISTING` so `_best_match` consumes either.
_NEAREST_ENTITIES = """
FOR e IN entities
  FILTER e.tenant_id == @tenant_id
  LET score = APPROX_NEAR_COSINE(e.embedding, @qvec, {nProbe: @nprobe})
  SORT score DESC
  LIMIT @topk
  RETURN { key: e._key, name: e.name, label: e.label, embedding: e.embedding,
           invalid: e.invalid_at != null }
"""

# IN-6: the union of top-k nearest existing entity KEYS across a chunk of query vectors, in ONE
# round trip (vs one `_NEAREST_ENTITIES` call per entity). The per-qvec SORT/LIMIT must sit in a
# subquery: at the top level of a nested FOR they apply to the whole stream, and ArangoDB 3.12.9
# then returned `topk` rows for the entire batch, not per vector. Keys only — DISTINCT over
# 1536-dim embedding objects was the memory hog; `_FETCH_BY_KEYS` loads each distinct row once.
_NEAREST_ENTITY_KEYS_BATCH = """
FOR qvec IN @qvecs
  FOR k IN (
    FOR e IN entities
      FILTER e.tenant_id == @tenant_id
      SORT APPROX_NEAR_COSINE(e.embedding, qvec, {nProbe: @nprobe}) DESC
      LIMIT @topk
      RETURN e._key
  )
    RETURN DISTINCT k
"""

_FETCH_BY_KEYS = """
FOR e IN entities
  FILTER e._key IN @keys
  RETURN { key: e._key, name: e.name, label: e.label, embedding: e.embedding,
           invalid: e.invalid_at != null }
"""

_TENANT_ENTITY_COUNT = """
RETURN LENGTH(FOR e IN entities FILTER e.tenant_id == @tenant_id RETURN 1)
"""

# Query vectors per `_NEAREST_ENTITY_KEYS_BATCH` round trip — bounds one query's work/memory
# when a batch extracts hundreds of distinct entities.
_ANN_QVEC_CHUNK = 64

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


# Company forms that don't tell two names apart ("Acme", "Acme Inc.", "Acme, LLC").
_LEGAL_SUFFIX = re.compile(
    r"[\s,]+(?:inc|incorporated|llc|llp|ltd|limited|corp|corporation|plc|gmbh)\.?$"
)


def name_key(name: str) -> str:
    """What makes two entity names the same one: the words, ignoring case, spacing, a
    possessive ending and a trailing legal suffix. The extractor's type is deliberately not
    part of it, since one name often comes back with different types."""
    folded = strip_possessive(" ".join(name.casefold().split())) or name.casefold()
    return _LEGAL_SUFFIX.sub("", folded).strip(" .,") or folded


def _entity_key(tenant_id: str, name: str) -> str:
    raw = f"{tenant_id}\x1f{name_key(name)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _by_name(pool: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for row in pool:
        index.setdefault(name_key(row["name"]), []).append(row)
    return index


def _name_match(rows: list[dict[str, Any]], label: str) -> str | None:
    """The key of the existing entity a name belongs to, from the rows sharing its
    `name_key`: the same label first, then any live one. A deprecated entity is only taken
    for its own label (the exact entity coming back)."""
    usable = [r for r in rows if r["label"] == label or not r.get("invalid")]
    if not usable:
        return None
    best = min(usable, key=lambda r: (r["label"] != label, bool(r.get("invalid")), r["key"]))
    return cast(str, best["key"])


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
    vectors = (row.get("embedding") for row in pool)  # a list, or an array from the cache
    p = _unit(np.asarray(
        [[0.0] * dim if v is None or len(v) == 0 else v for v in vectors], dtype=np.float64))
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


def _use_ann(db: StandardDatabase, tenant_id: str, *, dimensions: int) -> bool:
    """Resolve via the ANN index only when it is warm AND this tenant is big enough to need it.
    Filtered ANN widens its search until it finds top-k rows of *this* tenant, so its cost
    scales with the other tenants sharing the collection; the exact tenant scan costs only the
    tenant's own count. Gating on the collection's count alone (the index lifecycle's concern)
    is what OOM-killed a many-tenant eval database (DESIGN §7, SC-1b)."""
    if not vector_index_ready(
        db, dimensions=dimensions, n_lists=settings.entity_vector_n_lists,
        train_factor=settings.entity_vector_train_factor, collection="entities",
    ):
        return False
    count = cast(Cursor, db.aql.execute(_TENANT_ENTITY_COUNT, bind_vars={"tenant_id": tenant_id}))
    return int(next(iter(count), 0)) > settings.entity_resolution_scan_max


def _resolution_pool(
    db: StandardDatabase, tenant_id: str, qvecs: list[list[float]], *, use_ann: bool
) -> list[dict[str, Any]]:
    """Candidate existing-entity rows to resolve this batch against (IN-6). ANN → the union of
    every query vector's top-k nearest keys (chunked), then one fetch of those rows; a superset
    of each vector's own top-k, so best-match is unchanged. Otherwise a single tenant scan. Any
    ANN fault falls back to the scan — never breaks ingest."""
    if use_ann and qvecs:
        try:
            keys: set[str] = set()
            for i in range(0, len(qvecs), _ANN_QVEC_CHUNK):
                bind: dict[str, Any] = {
                    "tenant_id": tenant_id, "qvecs": qvecs[i:i + _ANN_QVEC_CHUNK],
                    "topk": settings.entity_resolution_top_k, "nprobe": settings.n_probe,
                }
                cur = db.aql.execute(_NEAREST_ENTITY_KEYS_BATCH, bind_vars=bind)
                keys.update(cast(Cursor, cur))
            rows = db.aql.execute(_FETCH_BY_KEYS, bind_vars={"keys": list(keys)})
            return list(cast(Cursor, rows))
        except Exception as exc:  # noqa: BLE001 — ANN fault → scan, never break ingest
            forget_vector_state(db, "entities")  # the index may have been dropped
            logger.warning("entity ANN resolution failed; scanning",
                           extra={"reason": type(exc).__name__, "detail": str(exc)})
    return _scan_pool(db, tenant_id)


def _scan_pool(db: StandardDatabase, tenant_id: str) -> list[dict[str, Any]]:
    """Every entity of the tenant with its embedding, reusing the cached ones (PF-1h)."""
    max_bytes = settings.entity_vector_cache_mb * 1024 * 1024
    if max_bytes <= 0:
        cur = db.aql.execute(_FETCH_EXISTING, bind_vars={"tenant_id": tenant_id})
        return list(cast(Cursor, cur))
    import numpy as np

    scope = (db.name, tenant_id)
    held = entity_vectors.get(scope)
    known: dict[str, dict[str, int]] = {}
    for key, (stamp, _) in held.items():
        known.setdefault(stamp, {})[key] = 1
    bind: dict[str, Any] = {"tenant_id": tenant_id, "known": known}
    # allow_retry: a batch lost to a dropped connection can be asked for again (`transport`)
    cur = cast(Cursor, db.aql.execute(
        _FETCH_UNCACHED, bind_vars=bind, batch_size=_SCAN_BATCH_SIZE, allow_retry=True,
    ))
    rows = list(cur)
    if cur.id is not None:  # such a cursor stays on the server until closed or expired
        cur.close(ignore_missing=True)
    entry: Entry = {}
    for row in rows:
        if row["cached"]:
            row["embedding"] = held[row["key"]][1]
        elif row["embedding"]:
            row["embedding"] = np.asarray(row["embedding"], dtype=np.float64)
        else:
            continue  # nothing to cache; it scores as a zero vector
        entry[row["key"]] = (row["stamp"], row["embedding"])
    entity_vectors.put(scope, entry, max_bytes)
    return rows


def _edge_doc(
    key: str, from_id: str, to_id: str, now: str, tenant_id: str, **extra: Any
) -> dict[str, Any]:
    return {
        "_key": key,
        "_from": from_id,
        "_to": to_id,
        "tenant_id": tenant_id,
        "ingestion_time": now,
        "valid_time": now,          # defaults to ingestion_time (§4; explicit parse → 3e)
        "valid_time_explicit": False,
        "invalid_at": None,
        "weight": 1.0,              # EWA computation deferred (§12)
        **extra,
    }


def _edge(
    db: StandardDatabase,
    collection: str,
    key: str,
    from_id: str,
    to_id: str,
    tenant_id: str,
    **extra: Any,
) -> None:
    doc = _edge_doc(key, from_id, to_id, utcnow_iso(), tenant_id, **extra)
    retry_on_conflict(
        lambda: db.collection(collection).insert(doc, overwrite_mode="ignore", silent=True))


def _relate(
    db: StandardDatabase,
    *,
    key: str,
    from_id: str,
    to_id: str,
    tenant_id: str,
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
        "tenant_id": tenant_id,
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
    retry_on_conflict(lambda: db.aql.execute(_RELATE, bind_vars=bind))


def extract_entities(
    extractor: Extractor, content: str, timer: StageTimer
) -> list[ExtractedEntity]:
    """The extractor's entities for one memory, timed as `<timer>.extract`."""
    with timer.stage("extract"):
        return list(extractor.extract(content))


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
    extracted: list[ExtractedEntity] | None = None,
    vectors: Mapping[str, list[float]] | None = None,
) -> list[str]:
    """Extract, dedupe/flag, persist entities + edges. Returns resolved entity keys.

    A caller that already ran the extractor passes `extracted`, and `vectors` (by name) if
    it embedded the names too; whatever is missing is computed here.

    `source_reliability` (0..1) weights how much this episode corroborates each
    fact: it accumulates into `reliability_sum` and drives `belief` (§8/§12).
    """
    timer = StageTimer("write.entities")  # per-write sums → /health write.entities.<stage>
    if extracted is None:
        extracted = extract_entities(extractor, content, timer)
    if not extracted:
        timer.record()
        return []

    # SC-1b: use the ANN index (top-k nearest) once it is warm and the tenant is large; below
    # that, scan the tenant's entities once (cached, lazy) as before.
    use_ann = _use_ann(db, tenant_id, dimensions=embedder.dimensions)
    scan_cache: list[dict[str, Any]] | None = None

    def _candidates(qvec: list[float]) -> list[dict[str, Any]]:
        nonlocal scan_cache
        if use_ann and qvec:
            try:
                ann_bind: dict[str, Any] = {
                    "tenant_id": tenant_id, "qvec": qvec,
                    "topk": settings.entity_resolution_top_k, "nprobe": settings.n_probe,
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
    if vectors is None:
        with timer.stage("embed"):
            vectors = embed_batch_cached(
                embedder, [ent.name for ent in extracted], tenant_id=tenant_id
            )

    key_by_name_key: dict[str, str] = {}  # one corroboration per name in this episode
    for ent in extracted:
        nk = name_key(ent.name)
        if (seen := key_by_name_key.get(nk)) is not None:
            key_by_entity[(ent.name, ent.label)] = seen
            key_by_name.setdefault(ent.name, seen)
            continue
        vec = vectors[ent.name]
        with timer.stage("candidates"):
            candidates = _candidates(vec)
        with timer.stage("match"):
            same = _name_match(_by_name(candidates).get(nk, []), ent.label)
            match, sim = _best_match(vec, candidates, exclude=(ent.name, ent.label))
            if same is None and match is not None and sim >= settings.entity_merge_threshold:
                same = cast(str, match["key"])  # semantic duplicate under another name

        if same is not None:
            inc_bind: dict[str, Any] = {"key": same, "now": now, "rel": rel, "base": base}
            with timer.stage("upsert"):
                retry_on_conflict(partial(db.aql.execute, _INCREMENT, bind_vars=inc_bind))
            key = same
        else:
            needs_review = match is not None and sim >= settings.entity_flag_threshold
            detected += int(needs_review)
            doc = _entity_doc(
                ent, vec, tenant_id, agent_id, embedder, now, needs_review, match, explicit_vt,
                rel, base,
            )
            up_bind: dict[str, Any] = {
                "key": doc["_key"],
                "doc": doc,
                "now": now,
                "rel": rel,
                "base": base,
            }
            with timer.stage("upsert"):
                cursor = cast(Cursor, retry_on_conflict(
                    partial(db.aql.execute, _UPSERT_ENTITY, bind_vars=up_bind)))
                key = cast(str, next(iter(cursor)))

        key_by_name_key[nk] = key
        key_by_entity[(ent.name, ent.label)] = key
        key_by_name.setdefault(ent.name, key)
        with timer.stage("edges"):
            _edge(
                db, "mentions", f"{memory_key}__{key}", f"memories/{memory_key}",
                f"entities/{key}", tenant_id,
            )
            _edge(
                db, "produced_by", f"{key}__{episode_key}",
                f"entities/{key}", f"episodes/{episode_key}", tenant_id,
            )

    # One relation per entity pair (typed relationship wins over co-occurrence),
    # corroborated exactly once per episode so the count tracks independent episodes.
    pairs: dict[tuple[str, str], str] = {}
    with timer.stage("relations"):
        typed_relations = list(extractor.extract_relations(content, extracted))
    for relation in typed_relations:
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
    with timer.stage("relate"):
        for (lo, hi), relationship in pairs.items():
            _relate(
                db, key=f"{lo}__{hi}", from_id=f"entities/{lo}", to_id=f"entities/{hi}",
                tenant_id=tenant_id, relationship=relationship, rel=rel, base=base, now=now,
                valid_time=explicit_vt,
            )

    if detected:
        metrics.emit("conflict", detected=detected)

    timer.record()
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
        "_key": _entity_key(tenant_id, ent.name),
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
  UPSERT { _key: row.key }
  INSERT row.doc
  UPDATE {
    mention_count: OLD.mention_count + row.count,
    reliability_sum: NOT_NULL(OLD.reliability_sum, 0) + row.rel_sum,
    belief: OLD.confidence * (1 - POW(1 - @base, NOT_NULL(OLD.reliability_sum, 0) + row.rel_sum)),
    accessed_at: @now
  }
  IN entities
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

# A batch's edges in one round trip, all or nothing: insert the mentions and produced_by edges
# (idempotent), and upsert relates_to. That upsert has the same shape as _RELATE, but
# corroboration/reliability accumulate the batch totals. The EWA weight is folded once (Δt≈0
# within a batch — a recency heuristic).
_BULK_EDGES = """
LET mentioned = (
  FOR doc IN @mentions
    INSERT doc INTO mentions OPTIONS { overwriteMode: "ignore" }
)
LET produced = (
  FOR doc IN @produced
    INSERT doc INTO produced_by OPTIONS { overwriteMode: "ignore" }
)
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
    of two differently-named-but-similar NEW entities is left to consolidation — names with the
    same `name_key` are still one entity; (2) the EWA edge weight (a recency heuristic, not a
    correctness invariant) folds once per batch (Δt≈0)."""
    if not memories:
        return {}
    timer = StageTimer("write_many.entities")  # /health write_many.entities.<stage>
    now = utcnow_iso()
    base = settings.corroboration_base
    result: dict[str, list[str]] = {mem.memory_key: [] for mem in memories}

    # 1. extract per memory. The model calls are inherent, but they're independent per memory,
    #    so an I/O-bound extractor runs them under a bounded thread pool (IN-7) — the sequential
    #    loop was the graph-on wall for haiku (one LLM call per turn, ~O(turns) round trips).
    #    CPU-bound extractors (spaCy/GLiNER/fake) stay sequential: they hold the GIL, so threads
    #    only contend (spaCy: 42s at 1 thread vs 2m14s at 8, mostly system time).
    #    `map` preserves order, so the downstream accumulation is byte-identical to the loop.
    def _extract_one(mem: GraphMemory) -> _PerMem:
        # Fail-soft: one memory's extraction failing (e.g. a transient LLM 5xx partway through a
        # long batch) must not crash the whole batch — log it and contribute no entities for that
        # memory. The record (memory + embedding) is already stored; it just misses the graph.
        try:
            ents = extractor.extract(mem.content)
            rels = extractor.extract_relations(mem.content, ents)
        except Exception as exc:  # noqa: BLE001 — degrade on any extractor fault, never break ingest
            logger.warning("entity extraction failed; skipping this memory's entities",
                           extra={"reason": type(exc).__name__, "detail": str(exc)})
            ents, rels = [], []
        return (mem, ents, rels, parse_explicit_time(mem.content))

    workers = min(settings.extraction_concurrency, len(memories)) if is_io_bound(extractor) else 1
    with timer.stage("extract"):
        if workers <= 1:
            per_mem = [_extract_one(mem) for mem in memories]
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                per_mem = list(executor.map(_extract_one, memories))

    names = [e.name for _, ents, _, _ in per_mem for e in ents]
    if not names:
        timer.record()
        return result
    with timer.stage("embed"):
        vec_by_name = embed_batch_cached(embedder, names, tenant_id=tenant_id)

    # 2. accumulate per distinct name (`name_key`) across the batch. The first typed form
    #    stands for the name: it is what a new entity is called.
    accum: dict[str, _EntityAccum] = {}
    for mem, ents, _rels, vt in per_mem:
        in_memory: dict[str, ExtractedEntity] = {}  # per-memory dedup
        for ent in ents:
            kept = in_memory.setdefault(name_key(ent.name), ent)
            if kept.label == "Concept" and ent.label != "Concept":
                in_memory[name_key(ent.name)] = ent
        for nk, ent in in_memory.items():
            a = accum.get(nk)
            if a is None:
                a = accum[nk] = _EntityAccum(ent=ent, vec=vec_by_name[ent.name], valid_time=vt)
            elif a.ent.label == "Concept" and ent.label != "Concept":
                a.ent, a.vec = ent, vec_by_name[ent.name]
            a.count += 1
            a.rel_sum += mem.source_reliability
            a.mem_refs.append((mem.memory_key, mem.episode_key))

    # 3. resolve distinct entities against existing DB entities — one candidate-pool fetch,
    #    then by name, else one vectorized (numpy) match, instead of a lookup + a Python cosine
    #    per entity (IN-6). The per-entity ANN query / O(cardinality²) scan loop was the
    #    graph-on ingest wall a real (high-cardinality) extractor exposed; folding it into a
    #    batch keeps it ~O(N·M) matmul in C. Belief/corroboration still fold by sum.
    nks = list(accum.keys())
    qvecs = [accum[nk].vec for nk in nks]
    with timer.stage("pool"):  # the candidate fetch: index-state check + ANN/scan query
        use_ann = _use_ann(db, tenant_id, dimensions=embedder.dimensions)
        pool = _resolution_pool(db, tenant_id, qvecs, use_ann=use_ann)
    with timer.stage("match"):
        named = _by_name(pool)
        matches = _best_match_many(
            qvecs, pool, exclude=[(accum[nk].ent.name, accum[nk].ent.label) for nk in nks])

    key_by_nk: dict[str, str] = {}
    increments: dict[str, list[float]] = {}  # existing key -> [count, rel_sum]
    own: dict[str, tuple[_EntityAccum, dict[str, Any] | None, bool]] = {}
    detected = 0
    for nk, (match, sim) in zip(nks, matches, strict=True):
        a = accum[nk]
        same = _name_match(named.get(nk, []), a.ent.label)
        if same is None and match is not None and sim >= settings.entity_merge_threshold:
            same = cast(str, match["key"])  # semantic duplicate under another name
        if same is not None:
            key_by_nk[nk] = same
            inc = increments.setdefault(same, [0.0, 0.0])
            inc[0] += a.count
            inc[1] += a.rel_sum
        else:
            needs_review = match is not None and sim >= settings.entity_flag_threshold
            detected += int(needs_review)
            own[nk] = (a, match, needs_review)

    # 4. bulk increment semantic-merge targets.
    if increments:
        inc_rows = [{"key": k, "count": c, "rel_sum": r} for k, (c, r) in increments.items()]
        inc_bind: dict[str, Any] = {"rows": inc_rows, "now": now, "base": base}
        with timer.stage("increment"):
            retry_on_conflict(lambda: db.aql.execute(_BULK_INCREMENT, bind_vars=inc_bind))

    # 5. bulk upsert own entities, under the keys their names give them.
    if own:
        up_rows: list[dict[str, Any]] = []
        for nk, (a, match, needs_review) in own.items():
            doc = _entity_doc(a.ent, a.vec, tenant_id, agent_id, embedder, now,
                              needs_review, match, a.valid_time, a.rel_sum, base)
            doc["mention_count"] = a.count  # INSERT carries the batch total (UPDATE adds it)
            up_rows.append({"key": doc["_key"], "doc": doc,
                            "count": a.count, "rel_sum": a.rel_sum})
            key_by_nk[nk] = doc["_key"]
        up_bind: dict[str, Any] = {"rows": up_rows, "now": now, "base": base}
        with timer.stage("upsert"):
            retry_on_conflict(lambda: db.aql.execute(_BULK_UPSERT_ENTITY, bind_vars=up_bind))

    # 6. build the mention + produced_by edges.
    mention_edges: list[dict[str, Any]] = []
    produced_edges: list[dict[str, Any]] = []
    for nk, a in accum.items():
        key = key_by_nk[nk]
        for mkey, ekey in a.mem_refs:
            mention_edges.append(_edge_doc(
                f"{mkey}__{key}", f"memories/{mkey}", f"entities/{key}", now, tenant_id))
            produced_edges.append(_edge_doc(
                f"{key}__{ekey}", f"entities/{key}", f"episodes/{ekey}", now, tenant_id))
            result[mkey].append(key)
    # 7. aggregate relates_to across the batch (typed wins; co-occurrence capped per memory).
    pair_accum: dict[tuple[str, str], dict[str, Any]] = {}
    for mem, ents, rels, vt in per_mem:
        mpairs: dict[tuple[str, str], str] = {}
        for r in rels:
            ka, kb = key_by_nk.get(name_key(r.source)), key_by_nk.get(name_key(r.target))
            if not ka or not kb or ka == kb:
                continue
            lo, hi = sorted((ka, kb))
            mpairs[(lo, hi)] = r.relationship
        for left, right in cooccurring_pairs(ents, max_pairs=settings.graph_max_pairs_per_turn):
            ka = key_by_nk.get(name_key(left.name))
            kb = key_by_nk.get(name_key(right.name))
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
    rel_rows: list[dict[str, Any]] = []
    for (lo, hi), pa in pair_accum.items():
        vt = pa["vt"]
        doc = {
            "_key": f"{lo}__{hi}", "_from": f"entities/{lo}", "_to": f"entities/{hi}",
            "tenant_id": tenant_id, "relationship": pa["relationship"],
            "corroboration": pa["corr"],
            "reliability_sum": pa["rel_sum"], "belief": 1 - (1 - base) ** pa["rel_sum"],
            "ingestion_time": now, "valid_time": vt or now,
            "valid_time_explicit": vt is not None, "invalid_at": None,
            "weight": settings.weight_ewa_alpha, "last_seen": now,
        }
        rel_rows.append({"key": f"{lo}__{hi}", "doc": doc, "corr": pa["corr"],
                         "rel_sum": pa["rel_sum"], "relationship": pa["relationship"]})

    # 8. write all three kinds of edge.
    edge_bind: dict[str, Any] = {
        "mentions": mention_edges, "produced": produced_edges,
        "rows": rel_rows, "base": base, "now": now,
        "w_alpha": settings.weight_ewa_alpha, "w_neg_lam": -settings.weight_lambda}
    with timer.stage("edges"):
        retry_on_conflict(lambda: db.aql.execute(_BULK_EDGES, bind_vars=edge_bind))

    if detected:
        metrics.emit("conflict", detected=detected)
    timer.record()
    return {mkey: list(dict.fromkeys(keys)) for mkey, keys in result.items()}
