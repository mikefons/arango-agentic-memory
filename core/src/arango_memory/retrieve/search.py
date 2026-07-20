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
from ..schema.collections import (
    SEARCH_VIEW,
    ensure_vector_index,
    has_vector_index,
    vector_index_state,
    vector_training_threshold,
)
from ..telemetry import metrics, span
from ..telemetry.logging import logger
from .decompose import decompose
from .enrich import QueryCache, hyde, should_skip_retrieval
from .rerank import Reranker, get_reranker

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
     AND doc.agent_id IN @agent_ids
  FILTER doc.invalid_at == null
  SORT BM25(doc) * ({_DECAY}) DESC
  LIMIT @pool
  RETURN {{ key: doc._key, text: doc.text, score: BM25(doc), agent_id: doc.agent_id,
            embedding: doc.embedding, type: doc.type,
            strength: doc.strength, accessed_at: doc.accessed_at }}
"""

# Force the ArangoSearch view to index pending commits before returning, so a write
# is immediately visible to BM25 retrieval (MA-1 read-your-writes). arangosearch's
# `waitForSync` query option triggers the view commit; scoped to the tenant to keep
# it cheap. (Graph reads hit collections and are already immediately consistent; the
# vector arm updates on the Faiss index's own cadence and is not covered here.)
_VIEW_SYNC = f"""
FOR doc IN {SEARCH_VIEW}
  SEARCH doc.tenant_id == @tenant_id OPTIONS {{ waitForSync: true }}
  LIMIT 1 RETURN 1
"""


def force_view_sync(db: StandardDatabase, tenant_id: str) -> None:
    """Block until the BM25 search view reflects committed writes for this tenant."""
    db.aql.execute(_VIEW_SYNC, bind_vars={"tenant_id": tenant_id})

# APPROX_NEAR_COSINE requires the vector index; tenant/agent scoping is applied
# in the same loop so the shared index stays logically isolated (§7).
_VECTOR_QUERY = """
FOR doc IN memories
  FILTER doc.tenant_id == @tenant_id
     AND doc.agent_id IN @agent_ids
     AND doc.invalid_at == null
  LET score = APPROX_NEAR_COSINE(doc.embedding, @qvec)
  SORT score DESC
  LIMIT @pool
  RETURN { key: doc._key, text: doc.text, score: score, agent_id: doc.agent_id,
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
    // BFS + global vertex-uniqueness visits each reachable entity once, not once per
    // path — without it, hub entities cause a combinatorial path fan-out that dominated
    // retrieval latency (~900ms/query at 200 docs → ~110ms). `p` is then each entity's
    // shortest bridging path, which is what the hop/weight ranking below wants anyway.
    FOR related, redge, p IN 0..@hops ANY entity relates_to
      OPTIONS { bfs: true, uniqueVertices: "global" }
      FILTER related.invalid_at == null
      FOR mem IN 1..1 INBOUND related mentions
        FILTER mem.tenant_id == @tenant_id
           AND mem.agent_id IN @agent_ids
           AND mem.invalid_at == null
           AND mem._key NOT IN @seed_keys
        // mean EWA weight of the bridging relates_to edges (§12); 0 for the 0-hop self.
        LET path_w = LENGTH(p.edges) == 0 ? 0 : AVERAGE(p.edges[*].weight)
        // Aggregate only the scalar ranking fields — NOT the memory doc. All rows for a
        // given key are the same `mem`, so MAX() of its scalars returns that constant;
        // this avoids buffering the 1536-dim embedding of every path row through the
        // COLLECT (which blew the AQL memory limit on a real-embedding corpus, MA-8).
        COLLECT key = mem._key AGGREGATE hops = MIN(LENGTH(p.edges)),
                                          belief = MAX(related.belief),
                                          centrality = MAX(related.centrality),
                                          weight = MAX(path_w),
                                          accessed_at = MAX(mem.accessed_at),
                                          strength = MAX(mem.strength)
        // closer hops rank higher, scaled 0.5–1.0 by the bridge's salience —
        // the strongest of corroboration (belief, §12), PageRank centrality (§9),
        // or recency-weighted relation strength (EWA weight, §12) — then decayed by
        // the connected memory's freshness (§11, lazy decay).
        LET salience = MAX([NOT_NULL(belief, 0), NOT_NULL(centrality, 0), NOT_NULL(weight, 0)])
        LET age_days = DATE_DIFF(NOT_NULL(accessed_at, @now), @now, "s") / 86400.0
        LET decay = NOT_NULL(strength, 1.0) * EXP(@neg_lam * age_days)
        LET score = (1.0 / (1.0 + hops)) * (0.5 + 0.5 * salience) * decay
        SORT score DESC
        LIMIT @pool
        // Fetch the heavy fields (text/embedding) only for the surviving pool — a point
        // lookup per row, so embeddings never enter the traversal's memory footprint.
        LET doc = DOCUMENT("memories", key)
        RETURN { key: key, score: score, agent_id: doc.agent_id,
                 text: doc.text, embedding: doc.embedding, type: doc.type,
                 strength: strength, accessed_at: accessed_at }
"""

_ENCODER = tiktoken.get_encoding("cl100k_base")

_RRF_K = 60
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
    agent_id: str = ""  # provenance: which agent wrote it (MA-2 multi-agent reads)
    key: str = ""  # memory _key — internal (not exposed over the API); used by prime (MA-3)


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
    agent_id: str = ""


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


def _arm_weight(name: str) -> float:
    """RRF weight per arm (§9).

    RRF assumes every input list ranks by the *same* notion of relevance. These arms don't:

    - **bm25** — ranks by "does this text answer this query". A true relevance ranker (1.0).
    - **vector** — ranks by embedding proximity to the *query*. That's topical similarity,
      which is only relevance when the query looks like the answer. On question→statement
      corpora it ranks noise (see `vector_weight`); HyDE (full mode) is the intended fix,
      since it embeds a hypothetical answer instead.
    - **graph** — ranks by hop distance × salience × decay: query-agnostic. A recall
      *expander*, not a ranker. At equal weight it buries the real hits (measured on
      LoCoMo: recall 0.06 at weight 1.0 vs 0.48 at 0.1).

    A mis-weighted arm doesn't just add noise — it *displaces* correct hits from the top-k,
    so an arm can score worse than useless. Tune per corpus.
    """
    return {
        "graph": settings.rrf_graph_weight,
        "vector": settings.rrf_vector_weight,
    }.get(name, 1.0)


def _rrf_fuse(ranked_lists: list[list[dict[str, Any]]], names: list[str]) -> list[_Candidate]:
    """Weighted reciprocal-rank fusion across ranked result lists, keyed by document."""
    by_key: dict[str, _Candidate] = {}
    for rows, name in zip(ranked_lists, names, strict=True):
        weight = _arm_weight(name)
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
                    agent_id=row.get("agent_id") or "",
                )
                by_key[key] = cand
            cand.signals.add(name)
            cand.fused_score += weight / (_RRF_K + rank)
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
        # Log the full message (MA-8): the class name alone hid the real cause.
        logger.warning(
            "retrieve degraded to bm25-only",
            extra={"reason": type(exc).__name__, "detail": str(exc)},
        )
        return []


def _mmr(
    candidates: list[_Candidate], k: int, *, lambda_: float | None = None
) -> list[_Candidate]:
    """Maximal-marginal-relevance re-rank (§9). `lambda_` in [0,1] balances relevance vs
    diversity (1.0 = pure relevance → best recall); defaults to `settings.mmr_lambda`.

    Relevance is the **fused RRF score** (min-max normalized), NOT a single arm's query
    cosine — otherwise MMR discards BM25's lexical wins that the fusion earned, collapsing
    recall (measured 0.56 → 0.18 on LoCoMo). Diversity is the max cosine to already-picked
    candidates, so near-duplicates are still spread.
    """
    lam = settings.mmr_lambda if lambda_ is None else lambda_
    if not candidates:
        return []
    lo = min(c.fused_score for c in candidates)
    hi = max(c.fused_score for c in candidates)
    span = (hi - lo) or 1.0
    selected: list[_Candidate] = []
    remaining = list(candidates)
    while remaining and len(selected) < k:
        best: _Candidate | None = None
        best_val = -math.inf
        for cand in remaining:
            relevance = (cand.fused_score - lo) / span
            diversity = max((_cos(cand.embedding, s.embedding) for s in selected), default=0.0)
            val = lam * relevance - (1.0 - lam) * diversity
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
    read_agent_ids: list[str] | None = None,
    k: int = 10,
    max_memory_tokens: int = 1500,
    embedder: Embedder | None = None,
    mode: str = "lite",
    candidate_pool: int = 100,
    n_lists: int | None = None,
    graph_hops: int | None = None,
    generator: Generator | None = None,
    cache: QueryCache | None = None,
    rerank: bool | None = None,
    reranker: Reranker | None = None,
) -> RetrieveResult:
    """Instrumented retrieval (DESIGN.md §18): span + metrics + §15 degradation.

    `read_agent_ids` (MA-2) widens the read across multiple agents in one fused pass
    (e.g. own + shared crew tiers); `None` reads just `agent_id`. Writes are unaffected.
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
                read_agent_ids=read_agent_ids,
                k=k,
                max_memory_tokens=max_memory_tokens,
                embedder=embedder,
                mode=mode,
                candidate_pool=candidate_pool,
                n_lists=n_lists,
                graph_hops=graph_hops,
                generator=generator,
                cache=cache,
                rerank=rerank,
                reranker=reranker,
            )
    except Exception as exc:  # noqa: BLE001 — §15: memory failures never break the turn
        metrics.emit("degraded", op="retrieve", reason=type(exc).__name__)
        # Log the full message (MA-8): the class name alone hid the AQL reason (e.g. an
        # under-trained IVF index), which cost a full day of benchmark triage.
        logger.warning(
            "retrieve degraded",
            extra={"reason": type(exc).__name__, "detail": str(exc)},
        )
        return RetrieveResult()
    metrics.emit(
        "retrieval",
        duration_ms=(time.perf_counter() - started) * 1000.0,
        results_k=len(result.hits),
        tokens_injected=result.tokens_injected,
        mode=mode,
    )
    return result


def _gather_fused(
    db: StandardDatabase,
    *,
    query: str,
    query_vec: list[float],
    agent_ids: list[str],
    tenant_id: str,
    candidate_pool: int,
    n_lists: int | None,
    graph_hops: int | None,
    dimensions: int,
    decay_binds: dict[str, Any],
    now: str,
) -> list[_Candidate]:
    """One query's arms (BM25 + vector + graph) → RRF fusion → recency boost.

    The per-query core of retrieval, sorted by the recency-decayed fused score.
    Multihop (RQ-1) calls this once per sub-query and fuses the results a second time.
    """
    scope = {"tenant_id": tenant_id, "agent_ids": agent_ids, "pool": candidate_pool}
    bm25_rows = _run(db, _BM25_QUERY, {"query": query, **scope, **decay_binds})

    ranked_lists = [bm25_rows]
    names = ["bm25"]
    # Self-healing cold start (§7): once the corpus is warm, the first retrieval
    # builds the index lazily; until then we run BM25-only. The one-time build
    # cost moves to the durable write path in Step 3.
    vector_ready = has_vector_index(db) or ensure_vector_index(
        db,
        dimensions=dimensions,
        n_lists=n_lists or settings.vector_n_lists,
        train_factor=settings.vector_train_factor,
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
                "agent_ids": agent_ids,
                "hops": graph_hops if graph_hops is not None else settings.graph_hops,
                "pool": candidate_pool,
                **decay_binds,
            },
        )
        if graph_rows:
            ranked_lists.append(graph_rows)
            names.append("graph")

    fused = _rrf_fuse(ranked_lists, names)

    # Recency/access boost (§9 stage 5, §11): decay the fused score by time since
    # last access, so stale memories sink in ranking and token-budget priority.
    # (The AQL arms above also fold decay into candidate *selection*; this uniform
    # pass weights final magnitude — RRF discards per-arm score magnitude.)
    for cand in fused:
        cand.fused_score *= effective_strength(
            cand.strength, cand.accessed_at or now, now, settings.decay_lambda
        )
    fused.sort(key=lambda c: -c.fused_score)
    return fused


def _fuse_candidate_lists(lists: list[list[_Candidate]]) -> list[_Candidate]:
    """Second-level RRF across sub-query candidate lists (RQ-1 multi-hop).

    Each list is already a full relevance ranking (bm25/vector/graph fused + recency),
    so all fuse at weight 1.0 — unlike the per-arm weighting inside `_gather_fused`. A
    document surfaced by several sub-questions accumulates RRF mass, which is exactly the
    multi-hop signal to reward. The merged candidate's fused_score is recomputed from
    rank alone; arm provenance (signals) is unioned for the hit `source`.
    """
    by_key: dict[str, _Candidate] = {}
    for cands in lists:
        for rank, cand in enumerate(cands, start=1):
            merged = by_key.get(cand.key)
            if merged is None:
                merged = _Candidate(
                    key=cand.key,
                    text=cand.text,
                    embedding=cand.embedding,
                    type=cand.type,
                    strength=cand.strength,
                    accessed_at=cand.accessed_at,
                    agent_id=cand.agent_id,
                )
                merged.signals = set(cand.signals)
                by_key[cand.key] = merged
            else:
                merged.signals |= cand.signals
            merged.fused_score += 1.0 / (_RRF_K + rank)
    return sorted(by_key.values(), key=lambda c: c.fused_score, reverse=True)


def _rerank(
    candidates: list[_Candidate], query: str, *, reranker: Reranker | None, top_n: int
) -> list[_Candidate]:
    """Cross-encoder rerank of the top-N fused candidates (RQ-2b). Replaces `fused_score`
    with the reranker's joint (query, text) relevance and reorders, so MMR then selects by
    relevance rather than fusion rank — the fix for in-pool-but-unranked golds (§23).

    Only the top-N are considered (the rest ranked below the pool cutoff can't reach top-k
    anyway); the recency-decayed fused score is intentionally *replaced* per the RQ-2b
    decision. Any reranker error degrades to the fused order (§15) — memory never breaks.
    """
    head = candidates[:top_n]
    if not head:
        return candidates
    try:
        rk = reranker or get_reranker()
        scores = rk.score(query, [c.text for c in head])
        if len(scores) != len(head):
            raise ValueError(f"reranker returned {len(scores)} scores for {len(head)} texts")
    except Exception as exc:  # noqa: BLE001 — §15: a rerank fault falls back, never breaks
        metrics.emit("degraded", op="rerank", reason=type(exc).__name__)
        logger.warning("rerank failed; using fused order",
                       extra={"reason": type(exc).__name__, "detail": str(exc)})
        return candidates
    for cand, score in zip(head, scores, strict=True):
        cand.fused_score = float(score)
    head.sort(key=lambda c: c.fused_score, reverse=True)
    return head


def _retrieve_impl(
    db: StandardDatabase,
    *,
    query: str,
    tenant_id: str,
    agent_id: str,
    read_agent_ids: list[str] | None = None,
    k: int = 10,
    max_memory_tokens: int = 1500,
    embedder: Embedder | None = None,
    mode: str = "lite",
    candidate_pool: int = 100,
    n_lists: int | None = None,
    graph_hops: int | None = None,
    generator: Generator | None = None,
    cache: QueryCache | None = None,
    rerank: bool | None = None,
    reranker: Reranker | None = None,
) -> RetrieveResult:
    """BM25 (+ vector when trained) → RRF → (rerank) → MMR → tiered token-budget assembly.

    Full mode adds the adaptive gate (may skip retrieval) and HyDE (embeds a
    hypothetical answer instead of the raw query) ahead of the core stages (§9).
    Multihop mode (RQ-1) decomposes the query into independent sub-lookups, gathers
    each *plus the original query*, and fuses the results a second time before the
    shared MMR/assembly tail — so it is a superset of the single-shot result.
    """
    emb = embedder or get_embedder()

    # Reference instant + decay rate shared by the AQL arms and the post-fusion pass.
    now = utcnow_iso()
    decay_binds = {"now": now, "neg_lam": -settings.decay_lambda}
    # Multi-agent read (MA-2): a 1-element list on the default path keeps the same plan.
    agent_ids = read_agent_ids or [agent_id]

    def gather(q: str, vec: list[float]) -> list[_Candidate]:
        """Run the per-query arm gather with this call's shared scope bound."""
        return _gather_fused(
            db,
            query=q,
            query_vec=vec,
            agent_ids=agent_ids,
            tenant_id=tenant_id,
            candidate_pool=candidate_pool,
            n_lists=n_lists,
            graph_hops=graph_hops,
            dimensions=emb.dimensions,
            decay_binds=decay_binds,
            now=now,
        )

    if mode == "multihop":
        # RQ-1: split into independent sub-lookups; ≤1 falls back to single-shot on the
        # original query (decompose() returns [query]), so a mis-fire never regresses.
        gen = generator or get_generator()
        subqueries = decompose(query, generator=gen, cache=cache)
        if len(subqueries) > 1:
            # Always fuse the *original* query's list alongside the sub-queries, so multihop
            # is a superset of single-shot: decomposition can only add evidence, never lose
            # a hit the full question would have found (RQ-1d). Sub-queries reach the extra
            # hops; the original anchors the direct-relevance hits for the gold turn.
            queries = [query, *subqueries]
            lists = [gather(q, _embed_query(emb, q, tenant_id=tenant_id)) for q in queries]
            fused = _fuse_candidate_lists(lists)
        else:
            fused = gather(query, _embed_query(emb, query, tenant_id=tenant_id))
    else:
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
                logger.warning(
                    "hyde failed; using query text", extra={"reason": type(exc).__name__}
                )
                query_vec = _embed_query(emb, query, tenant_id=tenant_id)
        else:
            query_vec = _embed_query(emb, query, tenant_id=tenant_id)
        fused = gather(query, query_vec)

    if not fused:
        return RetrieveResult()

    # Cross-encoder rerank (RQ-2b): re-score the top-N fused candidates by joint relevance
    # before MMR. Opt-in via the `rerank` flag or `settings.rerank_enabled`; off the lite
    # default. Degrades to the fused order on any reranker error (handled in `_rerank`).
    if settings.rerank_enabled if rerank is None else rerank:
        fused = _rerank(fused, query, reranker=reranker, top_n=settings.rerank_top_n)

    selected = _mmr(fused, k)
    selected.sort(key=lambda c: -c.fused_score)
    context, tokens = _assemble_tiered(selected, max_memory_tokens)

    # Spaced repetition (§11): refresh the memories actually surfaced (Δt → 0).
    reset_access(db, [c.key for c in selected])

    hits = [
        MemoryHit(text=c.text, score=round(c.fused_score, 6),
                  source="+".join(sorted(c.signals)), agent_id=c.agent_id, key=c.key)
        for c in selected
    ]
    return RetrieveResult(context=context, hits=hits, tokens_injected=tokens)


def diagnose_pool(
    db: StandardDatabase,
    *,
    query: str,
    tenant_id: str,
    agent_id: str,
    read_agent_ids: list[str] | None = None,
    candidate_pool: int = 100,
    n_lists: int | None = None,
    graph_hops: int | None = None,
    embedder: Embedder | None = None,
) -> list[MemoryHit]:
    """The full ranked fused candidate pool (BM25 ∪ vector ∪ graph → RRF → recency),
    *before* MMR and token-budget truncation — the lite single-shot pool `retrieve` picks
    its top-k from. Read-only, off the hot path; used by the RQ-2a miss diagnostic to tell
    whether a gold that missed top-k is still in the pool (a ranking failure) or absent
    entirely (a first-stage recall failure). Mirrors `diagnose_vector`.
    """
    emb = embedder or get_embedder()
    now = utcnow_iso()
    decay_binds = {"now": now, "neg_lam": -settings.decay_lambda}
    agent_ids = read_agent_ids or [agent_id]
    query_vec = _embed_query(emb, query, tenant_id=tenant_id)
    fused = _gather_fused(
        db,
        query=query,
        query_vec=query_vec,
        agent_ids=agent_ids,
        tenant_id=tenant_id,
        candidate_pool=candidate_pool,
        n_lists=n_lists,
        graph_hops=graph_hops,
        dimensions=emb.dimensions,
        decay_binds=decay_binds,
        now=now,
    )
    return [
        MemoryHit(text=c.text, score=round(c.fused_score, 6),
                  source="+".join(sorted(c.signals)), agent_id=c.agent_id, key=c.key)
        for c in fused
    ]


def diagnose_vector(
    db: StandardDatabase,
    *,
    query: str = "diagnostic probe",
    tenant_id: str = "diag",
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    """Vector-arm diagnostic (MA-8) — surfaces the *raw* failure the normal retrieve path
    swallows, for triaging a `retrieve degraded`.

    Reports corpus size vs. the training threshold and the index state, then runs the real
    retrieval via `_retrieve_impl` (which does not swallow, unlike `retrieve`). On failure
    it captures the exact exception string (e.g. the AQL reason for an under-trained IVF
    index) instead of the bare class name. Read-only apart from a lazy index build.
    """
    emb = embedder or get_embedder()
    n_lists = settings.vector_n_lists
    factor = settings.vector_train_factor
    corpus = cast(int, db.collection("memories").count())
    report: dict[str, Any] = {
        "corpus": corpus,
        "n_lists": n_lists,
        "train_factor": factor,
        "training_threshold": vector_training_threshold(n_lists, factor),
        "index_state": vector_index_state(db),
        "dimensions": emb.dimensions,
    }
    try:
        result = _retrieve_impl(
            db, query=query, tenant_id=tenant_id, agent_id=tenant_id, embedder=emb
        )
        report["ok"] = True
        report["hits"] = len(result.hits)
    except Exception as exc:  # noqa: BLE001 — the whole point is to expose the raw error
        report["ok"] = False
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
    return report
