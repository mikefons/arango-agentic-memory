"""Bi-temporal fields, Supersedes, and conflict-aware traversal (DESIGN.md §5, §12)."""

from __future__ import annotations

from collections.abc import Callable

from arango.database import StandardDatabase

from arango_memory.ingest.store import store
from arango_memory.lifecycle.conflict import supersede
from arango_memory.retrieve.search import RetrieveResult, retrieve


def _entities_by_name(db: StandardDatabase, tenant: str) -> dict[str, str]:
    rows = db.aql.execute(
        "FOR e IN entities FILTER e.tenant_id == @t RETURN e", bind_vars={"t": tenant}
    )
    return {r["name"]: r["_key"] for r in rows}


def test_entity_has_bitemporal_defaults(db: StandardDatabase) -> None:
    store(db, content="Acme launched", tenant_id="t_bt", agent_id="a")
    entity = next(
        db.aql.execute("FOR e IN entities FILTER e.tenant_id == 't_bt' RETURN e")
    )
    assert entity["valid_time"] == entity["ingestion_time"]
    assert entity["valid_time_explicit"] is False
    assert entity["invalid_at"] is None


def test_edges_have_bitemporal_defaults(db: StandardDatabase) -> None:
    store(db, content="Alice met Bob in Paris", tenant_id="t_be", agent_id="a")
    edge = next(db.aql.execute("FOR x IN mentions LIMIT 1 RETURN x"))
    assert edge["valid_time"] == edge["ingestion_time"]
    assert edge["valid_time_explicit"] is False
    assert edge["invalid_at"] is None
    assert edge["weight"] == 1.0


def test_supersede_writes_edge_and_soft_deprecates(db: StandardDatabase) -> None:
    store(db, content="Acme Corp and Globex Inc", tenant_id="t_sup", agent_id="a")
    keys = _entities_by_name(db, "t_sup")
    new_key, old_key = keys["Acme Corp"], keys["Globex Inc"]

    supersede(db, new_key=new_key, old_key=old_key)
    supersede(db, new_key=new_key, old_key=old_key)  # idempotent

    assert db.collection("Supersedes").count() == 1
    edge = db.collection("Supersedes").get(f"{new_key}__{old_key}")
    assert edge["_from"] == f"entities/{new_key}"
    assert edge["_to"] == f"entities/{old_key}"
    assert db.collection("entities").get(old_key)["invalid_at"] is not None
    assert db.collection("entities").get(new_key)["invalid_at"] is None


def test_graph_traversal_excludes_superseded_entity(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_gx", "agent_id": "a"}
    store(db, content="Alice joined Acme", turn_index=0, **ctx)
    store(db, content="Acme shipped widgets", turn_index=1, **ctx)
    wait_for_searchable(db, query="Alice joined", **ctx)

    # Baseline: m2 ("widgets") is reachable only via the shared Acme entity.
    base = retrieve(db, query="Alice", k=10, **ctx)
    assert any("widgets" in h.text for h in base.hits)

    # Soft-deprecate Acme → it must no longer bridge the graph.
    keys = _entities_by_name(db, "t_gx")
    supersede(db, new_key=keys["Alice"], old_key=keys["Acme"])

    after = retrieve(db, query="Alice", k=10, **ctx)
    assert not any("widgets" in h.text for h in after.hits)
