"""RQ-2a miss diagnostic: classifier (no DB) + a diagnose_pool / diagnose smoke (DB)."""

from __future__ import annotations

from collections.abc import Callable

from arango.database import StandardDatabase

from arango_memory.eval.locomo import QA, Sample, Turn
from arango_memory.eval.pool_diag import MissBreakdown, classify, diagnose
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult, diagnose_pool, retrieve


def test_classify_buckets_hit_ranking_recall() -> None:
    top = ["Alice joined Acme"]                       # only the first gold is in top-k
    pool = ["Alice joined Acme", "Bob works at Acme"]  # second gold is in the pool, not top-k
    support = ["joined Acme", "Bob works at Acme", "Carol sails boats"]
    # in top-k → hit; in pool but not top-k → ranking; absent → recall
    assert classify(top, pool, support) == ["hit", "ranking", "recall"]


def test_missbreakdown_counts() -> None:
    b = MissBreakdown(hit=3, ranking=2, recall=5)
    assert b.items == 10
    assert b.misses == 7


def test_diagnose_pool_is_a_superset_of_top_k(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_pool", "agent_id": "a"}
    for i in range(12):
        store(db, content=f"memory number {i} about widgets", turn_index=i, **ctx)
    wait_for_searchable(db, query="widgets", **ctx)

    top = retrieve(db, query="widgets", **ctx, k=3)
    pool = diagnose_pool(db, query="widgets", **ctx, candidate_pool=100)
    top_keys = {h.key for h in top.hits}
    pool_keys = {h.key for h in pool}
    assert len(pool) >= len(top.hits)
    assert top_keys <= pool_keys  # everything retrieved is present in the pool


def test_diagnose_end_to_end_classifies_support(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    # A question whose two supporting facts are both ingested → both should be HIT or in-pool,
    # and a fabricated third fact that was never stored → a RECALL miss.
    sample = Sample(
        sample_id="t_diag",
        sessions=[[
            Turn(speaker="Alice", text="Alice visited Portland in spring"),
            Turn(speaker="Bob", text="Bob presented the coral reef keynote"),
        ]],
        qa=[QA(question="What happened on the trip and at the talk?", answer="various",
               gold_facts=["Alice visited Portland", "coral reef keynote", "never stored fact"],
               category="multi-hop")],
    )
    result = diagnose(db, [sample])
    overall = result["__all__"]
    assert overall.items == 3
    assert overall.recall >= 1  # the fabricated fact is absent from the pool
    assert "multi-hop" in result
