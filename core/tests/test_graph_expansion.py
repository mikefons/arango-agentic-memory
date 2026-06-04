"""Integration tests for graph expansion in retrieval (DESIGN.md §9 stage 4)."""

from __future__ import annotations

from collections.abc import Callable

from arango.database import StandardDatabase

from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult, retrieve


def test_graph_expansion_surfaces_connected_memory(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_g", "agent_id": "a"}
    # m1 links Alice + Acme (relates_to); m2 mentions only Acme.
    store(db, content="Alice joined Acme", turn_index=0, **ctx)
    store(db, content="Acme shipped widgets", turn_index=1, **ctx)
    # Ensure m1 is BM25-findable so it becomes a graph seed.
    wait_for_searchable(db, query="Alice joined", **ctx)

    result = retrieve(db, query="Alice", **ctx, k=10)
    connected = [h for h in result.hits if "widgets" in h.text]
    assert connected, "m2 should be reached via the entity graph"
    assert "graph" in connected[0].source  # m2 has no lexical/vector match for 'Alice'


def test_graph_expansion_is_tenant_scoped(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    store(db, content="Alice joined Acme", turn_index=0, tenant_id="t_a", agent_id="a")
    store(db, content="Acme shipped widgets", turn_index=1, tenant_id="t_a", agent_id="a")
    # Same hub entity name in another tenant — must not bridge across tenants.
    store(db, content="Acme shipped gadgets", turn_index=0, tenant_id="t_b", agent_id="a")
    wait_for_searchable(db, query="Alice joined", tenant_id="t_a", agent_id="a")

    result = retrieve(db, query="Alice", tenant_id="t_a", agent_id="a", k=10)
    texts = [h.text for h in result.hits]
    assert any("widgets" in t for t in texts)
    assert not any("gadgets" in t for t in texts)


def test_retrieve_without_entities_still_works(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    # No capitalized tokens → no entities → no graph edges; BM25 path must still work.
    store(db, content="just some lowercase text here", tenant_id="t_ne", agent_id="a")
    result = wait_for_searchable(db, query="lowercase text", tenant_id="t_ne", agent_id="a")
    assert result.hits
    assert all(h.source == "bm25" for h in result.hits)
