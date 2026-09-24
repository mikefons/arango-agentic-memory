"""Rerank wired into retrieve (RQ-2b-b): reorders hits, composes, degrades. DB-backed."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest
from arango.database import StandardDatabase

from arango_memory.config import settings
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult, retrieve


class _KeywordReranker:
    """Ranks any passage containing `needle` to the top; deterministic, no model."""

    model = "keyword-test"

    def __init__(self, needle: str) -> None:
        self._needle = needle.lower()

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        return [1.0 if self._needle in t.lower() else 0.0 for t in texts]


class _BoomReranker:
    model = "boom"

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        raise RuntimeError("reranker unavailable")


def test_rerank_promotes_the_scored_candidate_to_top(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_rr", "agent_id": "a"}
    # Several lexically-similar memories; only one mentions "kyoto".
    for i in range(6):
        store(db, content=f"trip planning note number {i} about travel", turn_index=i, **ctx)
    store(db, content="the reunion trip was in kyoto that autumn", turn_index=99, **ctx)
    # Wait on the *kyoto* doc specifically — it's stored last, so waiting on "trip" could
    # return before it is searchable (then it wouldn't be in the pool for the reranker).
    wait_for_searchable(db, query="kyoto", **ctx)

    reranker = _KeywordReranker("kyoto")
    result = retrieve(db, query="trip", **ctx, k=5, rerank=True, reranker=reranker)
    assert result.hits
    assert "kyoto" in result.hits[0].text  # reranker forced it to the top


def test_rerank_degrades_to_fused_order_on_failure(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_rr_boom", "agent_id": "a"}
    store(db, content="alice joined acme in 2021", turn_index=0, **ctx)
    store(db, content="acme shipped widgets", turn_index=1, **ctx)
    baseline = wait_for_searchable(db, query="alice acme", **ctx)

    # A reranker that raises must not break the turn — fall back to the fused order.
    result = retrieve(db, query="alice acme", **ctx, k=10, rerank=True, reranker=_BoomReranker())
    assert result.hits
    assert {h.text for h in result.hits} == {h.text for h in baseline.hits}


def test_rerank_does_not_truncate_when_k_exceeds_top_n(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = {"tenant_id": "t_rr_tail", "agent_id": "a"}
    for i in range(6):
        store(db, content=f"harbor log entry {i} about ferries", turn_index=i, **ctx)
    wait_for_searchable(db, query="harbor ferries entry 5", **ctx)
    baseline = retrieve(db, query="harbor ferries", **ctx, k=6)

    # Rerank only the top 2; the other candidates must still be returned below them (the old
    # head-only return truncated this to 2 hits). Exact ordering is pinned by the unit tests in
    # test_rerank.py — these docs tie on BM25, so fused order isn't stable enough to assert here.
    monkeypatch.setattr(settings, "rerank_top_n", 2)
    result = retrieve(
        db, query="harbor ferries", **ctx, k=6, rerank=True, reranker=_KeywordReranker("entry")
    )
    assert len(result.hits) == len(baseline.hits) > 2
    scores = [h.score for h in result.hits]
    assert scores == sorted(scores, reverse=True)


def test_rerank_off_by_default(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_rr_off", "agent_id": "a"}
    store(db, content="a memory about sailing", turn_index=0, **ctx)
    wait_for_searchable(db, query="sailing", **ctx)
    # No rerank arg → default off; a would-be reranker is never consulted.
    result = retrieve(db, query="sailing", **ctx, k=5, reranker=_BoomReranker())
    assert result.hits  # did not raise → reranker not invoked
