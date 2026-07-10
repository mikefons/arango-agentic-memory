"""Multi-agent reads with single-pass fusion + provenance (MA-2, §14).

A reader can span several agents in one fused `retrieve`, so handoff works without
the caller stitching N calls together. Tenant isolation must still hold. Writes are
unaffected — `agent_id` stays the sole write identity.
"""

from __future__ import annotations

from arango.database import StandardDatabase

from arango_memory.crewai import crew_memory
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import (
    _BM25_QUERY,
    _GRAPH_QUERY,
    _VECTOR_QUERY,
    force_view_sync,
    retrieve,
)


def test_all_arms_scope_by_agent_ids() -> None:
    """Structural guard: every arm filters on the agent *list*, not a single id —
    so no arm silently reverts to single-agent scope."""
    for query in (_BM25_QUERY, _VECTOR_QUERY, _GRAPH_QUERY):
        assert "IN @agent_ids" in query
        assert "== @agent_id" not in query


def test_read_across_agents_returns_both_with_provenance(db: StandardDatabase) -> None:
    store(db, content="alpha the vault is in the crypt", tenant_id="t", agent_id="scout")
    store(db, content="bravo the cook was lying", tenant_id="t", agent_id="cook_watcher")
    force_view_sync(db, "t")

    result = retrieve(
        db, query="vault cook", tenant_id="t", agent_id="scout",
        read_agent_ids=["scout", "cook_watcher"],
    )
    by_agent = {h.agent_id: h.text for h in result.hits}
    assert "scout" in by_agent and "cook_watcher" in by_agent, "fused read missed an agent"
    assert "vault" in by_agent["scout"]
    assert "cook" in by_agent["cook_watcher"]


def test_default_read_is_single_agent(db: StandardDatabase) -> None:
    store(db, content="alpha private to scout", tenant_id="t", agent_id="scout")
    store(db, content="bravo private to watcher", tenant_id="t", agent_id="watcher")
    force_view_sync(db, "t")

    # No read_agent_ids → only the caller's own memories (unchanged behavior).
    result = retrieve(db, query="alpha bravo private", tenant_id="t", agent_id="scout")
    assert result.hits
    assert all(h.agent_id == "scout" for h in result.hits)
    assert all("watcher" not in h.text for h in result.hits)


def test_tenant_isolation_holds_across_agent_list(db: StandardDatabase) -> None:
    store(db, content="charlie secret fact", tenant_id="t1", agent_id="a")
    force_view_sync(db, "t1")

    # Same agent id under a different tenant must not surface t1's data.
    leaked = retrieve(
        db, query="charlie secret", tenant_id="t2", agent_id="a", read_agent_ids=["a"],
    )
    assert leaked.hits == []


def test_crew_memory_reads_across_tiers_in_one_call(db: StandardDatabase) -> None:
    mem = crew_memory(db, tenant_id="t", crew_id="research", agent_id="analyst")
    mem.interaction.save("delta my private note")          # → agent_id "analyst"
    mem.query.save("echo shared crew finding")             # → agent_id "research::query"
    force_view_sync(db, "t")

    # A search on any tier spans own + shared tiers (read_across), one fused pass.
    hits = mem.interaction.search("private shared note finding", limit=10)
    texts = " ".join(h["context"] for h in hits)
    agents = {h["metadata"]["agent_id"] for h in hits}
    assert "private" in texts and "shared" in texts, "tier search did not span namespaces"
    assert {"analyst", "research::query"} <= agents, "provenance should reflect real writers"
