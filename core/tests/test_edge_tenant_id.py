"""Every edge carries its tenant's `tenant_id`, like the vertices (all write paths + backfill)."""

from __future__ import annotations

from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.ingest.procedural import record_step
from arango_memory.ingest.store import StoreItem, store, store_many
from arango_memory.lifecycle.conflict import CrossTenantSupersede, supersede
from arango_memory.schema.migrations import _backfill_edge_tenant_id

_TURNS = ["Alice met Bob at Acme.", "Carol joined Acme."]


def _tenants(db: StandardDatabase, coll: str) -> list[str | None]:
    return list(db.aql.execute(f"FOR e IN {coll} RETURN e.tenant_id"))  # type: ignore[arg-type]


def _entity_key(db: StandardDatabase, tenant: str, name: str) -> str:
    cur = db.aql.execute(
        "FOR e IN entities FILTER e.tenant_id == @t AND e.name == @n RETURN e._key",
        bind_vars={"t": tenant, "n": name},
    )
    return str(next(iter(cur)))  # type: ignore[arg-type]


def test_sequential_store_tags_graph_edges(db: StandardDatabase) -> None:
    store(db, content="Alice met Bob at Acme.", tenant_id="et_seq", agent_id="a", extract=True)
    for coll in ("mentions", "produced_by", "relates_to"):
        tenants = _tenants(db, coll)
        assert tenants and set(tenants) == {"et_seq"}, coll


def test_batched_store_tags_graph_edges(db: StandardDatabase) -> None:
    store_many(
        db,
        [StoreItem(content=t, turn_index=i) for i, t in enumerate(_TURNS)],
        tenant_id="et_bat", agent_id="a", extract=True,
    )
    for coll in ("mentions", "produced_by", "relates_to"):
        tenants = _tenants(db, coll)
        assert tenants and set(tenants) == {"et_bat"}, coll


def test_procedural_edges_are_tagged(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "et_proc", "agent_id": "a"}
    memory_key = store(db, content="trigger", **ctx).memory_ids[0]
    k1 = record_step(db, tool_name="a", arguments={}, outcome="ok",
                     source_memory_key=memory_key, **ctx)
    record_step(db, tool_name="b", arguments={}, outcome="ok", prev_step_key=k1, **ctx)
    assert set(_tenants(db, "TOUCHED")) == {"et_proc"}
    assert set(_tenants(db, "TRANSITION")) == {"et_proc"}


def test_supersede_edge_is_tagged(db: StandardDatabase) -> None:
    store(db, content="Acme Corp and Globex Inc", tenant_id="et_sup", agent_id="a", extract=True)
    new_key = _entity_key(db, "et_sup", "Acme Corp")
    old_key = _entity_key(db, "et_sup", "Globex Inc")
    supersede(db, tenant_id="et_sup", new_key=new_key, old_key=old_key)
    assert set(_tenants(db, "Supersedes")) == {"et_sup"}


def test_supersede_rejects_other_tenants_entities(db: StandardDatabase, api: TestClient) -> None:
    store(db, content="Acme Corp and Globex Inc", tenant_id="et_a", agent_id="a", extract=True)
    store(db, content="Initech Ltd and Hooli Inc", tenant_id="et_b", agent_id="a", extract=True)
    mine = _entity_key(db, "et_a", "Acme Corp")
    theirs = _entity_key(db, "et_b", "Hooli Inc")

    try:
        supersede(db, tenant_id="et_a", new_key=mine, old_key=theirs)
    except CrossTenantSupersede:
        pass
    else:
        raise AssertionError("cross-tenant supersede must be rejected")

    ctx = {"tenant_id": "et_a", "agent_id": "a", "access_level": "write"}
    res = api.post("/v1/supersede", json={"new_key": mine, "old_key": theirs, "ctx": ctx})
    assert res.status_code == 404
    assert db.collection("entities").get(theirs)["invalid_at"] is None  # untouched
    assert db.collection("Supersedes").count() == 0


def test_backfill_stamps_legacy_edges_and_is_idempotent(db: StandardDatabase) -> None:
    store(db, content="Alice met Bob at Acme.", tenant_id="et_old", agent_id="a", extract=True)
    for coll in ("mentions", "produced_by", "relates_to"):  # simulate pre-tagging rows
        db.aql.execute(f"FOR e IN {coll} UPDATE e WITH {{tenant_id: null}} IN {coll} "
                       "OPTIONS {keepNull: false}")
        assert None in _tenants(db, coll)

    _backfill_edge_tenant_id(db)
    _backfill_edge_tenant_id(db)  # idempotent

    for coll in ("mentions", "produced_by", "relates_to"):
        assert set(_tenants(db, coll)) == {"et_old"}, coll
