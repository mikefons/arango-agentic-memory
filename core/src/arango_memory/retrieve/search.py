"""Core retrieval (DESIGN.md §9) — Step 2a.

Parallel BM25 + vector search → RRF fusion → MMR diversity → tiered token-budget
assembly. Vector search activates only when the Faiss IVF index is trained
(corpus ≥ n_lists); until then retrieval degrades to BM25-only (§7, §15).
Embeddings live on the documents regardless of index state, so MMR diversity
still applies in the BM25-only path. HyDE / adaptive gate (full mode) land in
Step 2b; graph expansion (needs entities/edges) lands in Step 3.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, cast

import tiktoken
from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..embedding import Embedder, get_embedder
from ..embedding_cache import embed_cached
from ..generation import Generator, get_generator
from ..lifecycle.decay import effective_strength, reset_access
from ..models import utcnow_iso
from ..schema.collections import SEARCH_VIEW, ensure_vector_index, has_vector_index
from ..telemetry import metrics, span
from ..telemetry.logging import logger
from .enrich import QueryCache, hyde, should_skip_retrieval

# Lazy decay (§11): the Ebbinghaus multiplier strength·exp(-λ·Δdays) is folded into
# the ranking SORT so freshness shapes *candidate selection* (pool membership), not
# only the post-fusion reorder. `@neg_lam` is -λ; `@now` is the reference instant.
_DECAY = (
    'NOT_NULL(doc.strength, 1.0) * '
    'EXP(@neg_lam * DATE_DIFF(NOT_NULL(doc.accessed_at, @now), @now, "s") / 86400.0)'
)

_BM25_QUERY = f"""
FOR doc IN {SEARCH_VIEW}
  SEARCH ANALYZER(doc.text IN TOKENS(@query, "text_en")
                  OR doc.prospective_queries IN TOKENS(@query, "text_en"), "text_en")
     AND doc.tenant_id == @tenant_id
     AND doc.agent_id == @agent_id
  FILTER doc.invalid_at == null
  SORT BM25(doc) * ({_DECAY}) DESC
  LIMIT @pool
  RETURN {{ key: doc._key, text: doc.text, score: BM25(doc),
            embedding: doc.embedding, type: doc.type,
            strength: doc.strength, accessed_at: doc.accessed_at }}
"""

# APPROX_NEAR_COSINE requires the vector index; tenant/agent scoping is applied
# in the same loop so the shared index stays logically isolated (§7).
_VECTOR_QUERY = """
FOR doc IN memories
  FILTER doc.tenant_id == @tenant_id
     AND doc.agent_id == @agent_id
     AND doc.invalid_at == null
  LET score = APPROX_NEAR_COSINE(doc.embedding, @qvec)
  SORT score DESC
  LIMIT @pool
  RETURN { key: doc._key, text: doc.text, score: score,
           embedding: doc.embedding, type: doc.type,
           strength: doc.strength, accessed_at: doc.accessed_at }
"""

# Graph expansion (§9 stage 4): from seed memories → their entities → relates_to
# neighbours (0..hops) → other memories mentioning those entities. Ranked by the
# minimum relates_to hop distance (closer = stronger).
_GRAPH_QUERY = """
FOR start IN @seed_ids
  FOR entity IN 1..1 OUTBOUND start mentions
    FILTER entity.invalid_at == null
    FOR related, redge, p IN 0..@hops ANY entity relates_to
      FILTER related.invalid_at == null
      FOR mem IN 1..1 INBOUND related mentions
        FILTER mem.tenant_id == @tenant_id
           AND mem.agent_id == @agent_id
           AND mem.invalid_at == null
           AND mem._key NOT IN @seed_keys
        // mean EWA weight of the bridging relates_to edges (§12); 0 for the 0-hop self.
        LET path_w = LENGTH(p.edges) == 0 ? 0 : AVERAGE(p.edges[*].weight)
        COLLECT key = mem._key AGGREGATE hops = MIN(LENGTH(p.edges)),
                                          belief = MAX(related.belief),
                                          centrality = MAX(related.centrality),
                                          weight = MAX(path_w) INTO rows = mem
        // closer hops rank higher, scaled 0.5–1.0 by the bridge's salience —
        // the strongest of corroboration (belief, §12), PageRank centrality (§9),
        // or recency-weighted relation strength (EWA weight, §12) — then decayed by
        // the connected memory's freshness (§11, lazy decay).
        LET salience = MAX([NOT_NULL(belief, 0), NOT_NULL(centrality, 0), NOT_NULL(weight, 0)])
        LET age_days = DATE_DIFF(NOT_NULL(rows[0].accessed_at, @now), @now, "s") / 86400.0
        LET decay = NOT_NULL(rows[0].strength, 1.0) * EXP(@neg_lam * age_days)
        LET score = (1.0 / (1.0 + hops)) * (0.5 + 0.5 * salience) * decay
        SORT score DESC
        LIMIT @pool
        RETURN { key: key, score: score,
                 text: rows[0].text, embedding: rows[0].embedding, type: rows[0].type,
                 strength: rows[0].strength, accessed_at: rows[0].accessed_at }
"""

_ENCODER = tiktoken.get_encoding("cl100k_base")

_RRF_K = 60
_MMR_LAMBDA = 0.5
_GRAPH_SEED_COUNT = 10

# Tier token budget as fractions of max_memory_tokens (§9: 400/700/300/100 of 1500).
_TIER_FRACTIONS = {"working": 0.267, "episodic": 0.467, "semantic": 0.20, "reasoning": 0.067}
_TIER_ORDER = ("working", "episodic", "semantic", "reasoning")
_TYPE_TO_TIER = {"working": "working", "episodic": "episodic", "semantic": "semantic",
                 "step": "reasoning"}


@dataclass
class MemoryHit:
    text: str
    score: float
    source: str = "bm25"


@dataclass
class RetrieveResult:
    context: str = ""
    hits: list[MemoryHit] = field(default_factory=list)
    tokens_injected: int = 0


@dataclass
class _Candidate:
    key: str
    text: str
    embedding: list[float]
    type: str
    signals: set[str] = field(default_factory=set)
    fused_score: float = 0.0
    strength: float = 1.0
    accessed_at: str = ""


def _count_tokens(text: str) -> int:
    return len(_ENCODER.encode(text))


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _run(db: StandardDatabase, query: str, bind_vars: dict[str, Any]) -> list[dict[str, Any]]:
    cursor = cast(Cursor, db.aql.execute(query, bind_vars=bind_vars))
    return [row for row in cursor]


def _seed_keys(ranked_lists: list[list[dict[str, Any]]]) -> list[str]:
    """Top memory keys across the lexical/vector lists, used as graph seeds."""
    seeds: list[str] = []
    for rows in ranked_lists:
        for row in rows[:_GRAPH_SEED_COUNT]:
            if row["key"] not in seeds:
                seeds.append(row["key"])
    return seeds


def _rrf_fuse(ranked_lists: list[list[dict[str, Any]]], names: list[str]) -> list[_Candidate]:
    """Reciprocal-rank fusion across ranked result lists, keyed by document."""
    by_key: dict[str, _Candidate] = {}
    for rows, name in zip(ranked_lists, names, strict=True):
        for rank, row in enumerate(rows, start=1):
            key = row["key"]
            cand = by_key.get(key)
            if cand is None:
                cand = _Candidate(
                    key=key,
                    text=row["text"],
                    embedding=row.get("embedding") or [],
                    type=row.get("type") or "episodic",
                    strength=row.get("strength", 1.0) or 1.0,
                    accessed_at=row.get("accessed_at") or "",
                )
                by_key[key] = cand
            cand.signals.add(name)
            cand.fused_score += 1.0 / (_RRF_K + rank)
    return sorted(by_key.values(), key=lambda c: c.fused_score, reverse=True)


def _embed_query(emb: Embedder, query: str, *, tenant_id: str) -> list[float]:
    """Embed the query; on embedder error degrade to BM25-only (§15).

    Returns an empty vector instead of raising, so the BM25 arm still runs (the
    vector arm is skipped and MMR becomes relevance-blind). Memory must never
    break the turn — an embedder outage just costs the vector signal.
    """
    try:
        return embed_cached(emb, query, tenant_id=tenant_id)
    except Exception as exc:  # noqa: BLE001 — §15: embedder down → BM25-only
        metrics.emit("degraded", op="retrieve", reason=type(exc).__name__)
        logger.warning("retrieve degraded to bm25-only", extra={"reason": type(exc).__name__})
        return []


def _mmr(query_emb: list[float], candidates: list[_Candidate], k: int) -> list[_Candidate]:
    """Maximal-marginal-relevance re-rank for diversity (§9)."""
    selected: list[_Candidate] = []
    remaining = list(candidates)
    while remaining and len(selected) < k:
        best: _Candidate | None = None
        best_val = -math.inf
        for cand in remaining:
            relevance = _cos(query_emb, cand.embedding)
            diversity = max((_cos(cand.embedding, s.embedding) for s in selected), default=0.0)
            val = _MMR_LAMBDA * relevance - (1.0 - _MMR_LAMBDA) * diversity
            if val > best_val:
                best_val, best = val, cand
        assert best is not None
        selected.append(best)
        remaining.remove(best)
    return selected


def _assemble_tiered(hits: list[_Candidate], max_tokens: int) -> tuple[str, int]:
    """Tiered token-budget assembly with roll-up of unused budget (§9)."""
    caps = {tier: int(frac * max_tokens) for tier, frac in _TIER_FRACTIONS.items()}
    tier_used = dict.fromkeys(_TIER_FRACTIONS, 0)
    chosen: list[_Candidate] = []
    total = 0

    ordered = sorted(
        hits, key=lambda c: (_TIER_ORDER.index(_TYPE_TO_TIER.get(c.type, "episodic")),
                             -c.fused_score)
    )
    # Pass 1 — respect per-tier caps.
    for cand in ordered:
        tier = _TYPE_TO_TIER.get(cand.type, "episodic")
        cost = _count_tokens(cand.text)
        if total + cost <= max_tokens and tier_used[tier] + cost <= caps[tier]:
            chosen.append(cand)
            tier_used[tier] += cost
            total += cost
    # Pass 2 — roll unused budget up across tiers, by score.
    for cand in sorted(hits, key=lambda c: -c.fused_score):
        if cand in chosen:
            continue
        cost = _count_tokens(cand.text)
        if total + cost <= max_tokens:
            chosen.append(cand)
            total += cost

    chosen.sort(key=lambda c: -c.fused_score)
    context = "\n".join(f"- {c.text}" for c in chosen)
    return context, total


def retrieve(
    db: StandardDatabase,
    *,
    query: str,
    tenant_id: str,
    agent_id: str,
    k: int = 10,
    max_memory_tokens: int = 1500,
    embedder: Embedder | None = None,
    mode: str = "lite",
    candidate_pool: int = 100,
    n_lists: int | None = None,
    graph_hops: int | None = None,
    generator: Generator | None = None,
    cache: QueryCache | None = None,
) -> RetrieveResult:
    """Instrumented retrieval (DESIGN.md §18): span + metrics + §15 degradation.

    Any failure degrades to an empty (memory-less) result and a `degraded` event,
    so a memory fault never breaks the agent turn.
    """
    started = time.perf_counter()
    try:
        with span("memory.retrieve", mode=mode):
            result = _retrieve_impl(
                db,
                query=query,
                tenant_id=tenant_id,
                agent_id=agent_id,
                k=k,
                max_memory_tokens=max_memory_tokens,
                embedder=embedder,
                mode=mode,
                candidate_pool=candidate_pool,
                n_lists=n_lists,
                graph_hops=graph_hops,
                generator=generator,
                cache=cache,
            )
    except Exception as exc:  # noqa: BLE001 — §15: memory failures never break the turn
        metrics.emit("degraded", op="retrieve", reason=type(exc).__name__)
        logger.warning("retrieve degraded", extra={"reason": type(exc).__name__})
        return RetrieveResult()
    metrics.emit(
        "retrieval",
        duration_ms=(time.perf_counter() - started) * 1000.0,
        results_k=len(result.hits),
        tokens_injected=result.tokens_injected,
        mode=mode,
    )
    return result


def _retrieve_impl(
    db: StandardDatabase,
    *,
    query: str,
    tenant_id: str,
    agent_id: str,
    k: int = 10,
    max_memory_tokens: int = 1500,
    embedder: Embedder | None = None,
    mode: str = "lite",
    candidate_pool: int = 100,
    n_lists: int | None = None,
    graph_hops: int | None = None,
    generator: Generator | None = None,
    cache: QueryCache | None = None,
) -> RetrieveResult:
    """BM25 (+ vector when trained) → RRF → MMR → tiered token-budget assembly.

    Full mode adds the adaptive gate (may skip retrieval) and HyDE (embeds a
    hypothetical answer instead of the raw query) ahead of the core stages (§9).
    """
    emb = embedder or get_embedder()

    # Full-mode enrichment (§9 stages 1–2). The query vector is computed once
    # here and reused for both vector search and MMR relevance.
    if mode == "full":
        gen = generator or get_generator()
        if should_skip_retrieval(query, generator=gen, cache=cache):
            return RetrieveResult()
        try:
            query_vec = hyde(query, generator=gen, embedder=emb, cache=cache).embedding
        except Exception as exc:  # noqa: BLE001 — §15: skip HyDE, fall back to query text
            metrics.emit("degraded", op="retrieve", reason=type(exc).__name__)
            logger.warning("hyde failed; using query text", extra={"reason": type(exc).__name__})
            query_vec = _embed_query(emb, query, tenant_id=tenant_id)
    else:
        query_vec = _embed_query(emb, query, tenant_id=tenant_id)

    # Reference instant + decay rate shared by the AQL arms and the post-fusion pass.
    now = utcnow_iso()
    decay_binds = {"now": now, "neg_lam": -settings.decay_lambda}

    scope = {"tenant_id": tenant_id, "agent_id": agent_id, "pool": candidate_pool}
    bm25_rows = _run(db, _BM25_QUERY, {"query": query, **scope, **decay_binds})

    ranked_lists = [bm25_rows]
    names = ["bm25"]
    # Self-healing cold start (§7): once the corpus is warm, the first retrieval
    # builds the index lazily; until then we run BM25-only. The one-time build
    # cost moves to the durable write path in Step 3.
    vector_ready = has_vector_index(db) or ensure_vector_index(
        db, dimensions=emb.dimensions, n_lists=n_lists or settings.vector_n_lists
    )
    if vector_ready and query_vec:  # query_vec empty → embedder degraded, BM25-only (§15)
        vector_rows = _run(db, _VECTOR_QUERY, {"qvec": query_vec, **scope})
        ranked_lists.append(vector_rows)
        names.append("vector")

    # Graph expansion (§9 stage 4): traverse from the entities of the top hits to
    # surface connected memories that lexical/vector search alone would miss.
    seed_keys = _seed_keys(ranked_lists)
    if seed_keys:
        graph_rows = _run(
            db,
            _GRAPH_QUERY,
            {
                "seed_ids": [f"memories/{k}" for k in seed_keys],
                "seed_keys": seed_keys,
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "hops": graph_hops if graph_hops is not None else settings.graph_hops,
                "pool": candidate_pool,
                **decay_binds,
            },
        )
        if graph_rows:
            ranked_lists.append(graph_rows)
            names.append("graph")

    fused = _rrf_fuse(ranked_lists, names)
    if not fused:
        return RetrieveResult()

    # Recency/access boost (§9 stage 5, §11): decay the fused score by time since
    # last access, so stale memories sink in ranking and token-budget priority.
    # (The AQL arms above also fold decay into candidate *selection*; this uniform
    # pass weights final magnitude — RRF discards per-arm score magnitude.)
    for cand in fused:
        cand.fused_score *= effective_strength(
            cand.strength, cand.accessed_at or now, now, settings.decay_lambda
        )

    selected = _mmr(query_vec, fused, k)
    selected.sort(key=lambda c: -c.fused_score)
    context, tokens = _assemble_tiered(selected, max_memory_tokens)

    # Spaced repetition (§11): refresh the memories actually surfaced (Δt → 0).
    reset_access(db, [c.key for c in selected])

    hits = [
        MemoryHit(text=c.text, score=round(c.fused_score, 6), source="+".join(sorted(c.signals)))
        for c in selected
    ]
    return RetrieveResult(context=context, hits=hits, tokens_injected=tokens)
