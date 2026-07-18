"""Multi-hop retrieval (RQ-1b): second-level fusion + the multihop retrieve mode."""

from __future__ import annotations

from collections.abc import Callable

from arango.database import StandardDatabase

from arango_memory.generation import FakeGenerator
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import (
    RetrieveResult,
    _Candidate,
    _fuse_candidate_lists,
    retrieve,
)


def _cand(key: str, *, signals: set[str] | None = None) -> _Candidate:
    c = _Candidate(key=key, text=key, embedding=[], type="episodic")
    c.signals = signals or {"bm25"}
    return c


def test_fuse_ranks_a_doc_found_by_multiple_subqueries_highest() -> None:
    # c2 appears in both sub-query lists → accumulates RRF mass from both → ranks first,
    # which is the multi-hop signal: evidence corroborated across sub-questions wins.
    list_a = [_cand("c1"), _cand("c2")]
    list_b = [_cand("c3"), _cand("c2")]
    fused = _fuse_candidate_lists([list_a, list_b])
    assert fused[0].key == "c2"
    assert {c.key for c in fused} == {"c1", "c2", "c3"}  # deduped by key


def test_fuse_unions_arm_provenance() -> None:
    list_a = [_cand("c2", signals={"bm25"})]
    list_b = [_cand("c2", signals={"vector", "graph"})]
    fused = _fuse_candidate_lists([list_a, list_b])
    assert fused[0].signals == {"bm25", "vector", "graph"}


def test_multihop_surfaces_a_memory_the_single_query_cannot_reach(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_mh", "agent_id": "a"}
    # m1 and m2 share no entity (no graph bridge) and no token with the original question
    # — so neither the lexical nor the one-hop graph arm reaches m2 in a single shot. Only
    # a *decomposed* sub-query, aimed at the keynote, matches m2 lexically.
    store(db, content="Alice visited Portland", turn_index=0, **ctx)
    store(db, content="Keynote covered coral reefs", turn_index=1, **ctx)
    wait_for_searchable(db, query="Alice visited Portland", **ctx)

    question = "What did Alice see on her trip?"
    gen = FakeGenerator(
        handler=lambda prompt, system: "Where did Alice go?\nWhat did the keynote cover?"
    )

    lite = retrieve(db, query=question, **ctx, k=10)
    multi = retrieve(db, query=question, **ctx, k=10, mode="multihop", generator=gen)

    assert not any("coral" in h.text for h in lite.hits), "single-shot can't reach m2"
    assert any("coral" in h.text for h in multi.hits), "multihop reaches m2 via decomposition"


def test_multihop_falls_back_to_single_shot_on_one_subquery(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_mh_fb", "agent_id": "a"}
    store(db, content="Alice joined Acme in 2021", turn_index=0, **ctx)
    store(db, content="Acme shipped widgets", turn_index=1, **ctx)
    wait_for_searchable(db, query="Alice joined Acme", **ctx)

    # A single-line decomposition → decompose() returns [query] → identical to lite.
    gen = FakeGenerator(handler=lambda prompt, system: "just one lookup")
    lite = retrieve(db, query="Alice joined Acme", **ctx, k=10)
    multi = retrieve(db, query="Alice joined Acme", **ctx, k=10, mode="multihop", generator=gen)

    assert [h.text for h in multi.hits] == [h.text for h in lite.hits]
