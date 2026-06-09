"""Semantic-entity query + seed (DESIGN.md §11, §19)."""

from __future__ import annotations

from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.entity_api import get_entity, list_entities, seed
from arango_memory.ingest.store import store
from arango_memory.lifecycle.conflict import supersede


def _entity_id(db: StandardDatabase, tenant: str, name: str) -> str:
    return next(
        db.aql.execute(
            "FOR e IN entities FILTER e.tenant_id == @t AND e.name == @n RETURN e._key",
            bind_vars={"t": tenant, "n": name},
        )
    )


# ── get_entity ────────────────────────────────────────────
def test_get_entity_returns_entity_and_related(db: StandardDatabase) -> None:
    store(db, content="Alice met Bob", tenant_id="ent1", agent_id="a")
    result = get_entity(db, entity_id=_entity_id(db, "ent1", "Alice"), tenant_id="ent1")
    assert result is not None
    assert result["entity"]["name"] == "Alice"
    assert "embedding" not in result["entity"]  # §17: never expose embeddings
    assert "Bob" in {r["name"] for r in result["related"]}


def test_get_entity_none_for_unknown_or_other_tenant(db: StandardDatabase) -> None:
    store(db, content="Acme launched", tenant_id="ent2", agent_id="a")
    acme = _entity_id(db, "ent2", "Acme")
    assert get_entity(db, entity_id=acme, tenant_id="other") is None
    assert get_entity(db, entity_id="missing", tenant_id="ent2") is None


# ── list_entities ─────────────────────────────────────────
def test_list_entities_filters_and_hides_soft_deleted(db: StandardDatabase) -> None:
    store(db, content="Alice met Bob", tenant_id="ent3", agent_id="a")
    assert {r["name"] for r in list_entities(db, tenant_id="ent3")} >= {"Alice", "Bob"}
    assert all("embedding" not in r for r in list_entities(db, tenant_id="ent3"))
    assert list_entities(db, tenant_id="ent3", label="Person") == []  # fake → all "Concept"

    alice, bob = _entity_id(db, "ent3", "Alice"), _entity_id(db, "ent3", "Bob")
    supersede(db, new_key=bob, old_key=alice)  # Alice soft-deleted
    assert "Alice" not in {r["name"] for r in list_entities(db, tenant_id="ent3")}


# ── seed ──────────────────────────────────────────────────
def test_seed_creates_seed_entities(db: StandardDatabase) -> None:
    ids = seed(
        db,
        profile={"role": "data engineer", "domain": "logistics",
                 "preferences": ["dark mode", "vim"]},
        tenant_id="ent4",
        agent_id="a",
    )
    assert len(ids) == 4
    rows = {r["name"]: r for r in list_entities(db, tenant_id="ent4")}
    assert rows["data engineer"]["source"] == "seed"
    assert rows["data engineer"]["confidence"] == 0.6


def test_seed_does_not_clobber_observed(db: StandardDatabase) -> None:
    store(db, content="Logistics matters", tenant_id="ent5", agent_id="a")  # observed "Logistics"
    seed(db, profile={"domain": "Logistics"}, tenant_id="ent5", agent_id="a")

    logistics = next(
        db.aql.execute(
            "FOR e IN entities FILTER e.tenant_id == @t AND e.name == 'Logistics' RETURN e",
            bind_vars={"t": "ent5"},
        )
    )
    assert logistics["source"] == "observed"  # observed wins over seed
    assert logistics["confidence"] == 1.0


# ── HTTP endpoints ────────────────────────────────────────
def test_seed_and_entity_endpoints(api: TestClient) -> None:
    ctx = {"tenant_id": "eapi", "agent_id": "a", "access_level": "write"}
    body = {"profile": {"role": "analyst", "preferences": ["sql"]}, "ctx": ctx}
    seeded = api.post("/v1/seed", json=body)
    assert seeded.status_code == 200
    assert seeded.json()["status"] == "seeded"
    assert len(seeded.json()["entity_ids"]) == 2

    listed = api.get("/v1/entities", params={"tenant_id": "eapi"})
    assert listed.status_code == 200
    entities = listed.json()["entities"]
    assert {"analyst", "sql"} <= {e["name"] for e in entities}

    got = api.get("/v1/entity", params={"entity_id": entities[0]["id"], "tenant_id": "eapi"})
    assert got.status_code == 200
    assert got.json()["entity"]["id"] == entities[0]["id"]
    missing = api.get("/v1/entity", params={"entity_id": "nope", "tenant_id": "eapi"})
    assert missing.status_code == 404


def test_seed_requires_write(api: TestClient) -> None:
    ctx = {"tenant_id": "ew", "agent_id": "a", "access_level": "read"}
    assert api.post("/v1/seed", json={"profile": {"role": "x"}, "ctx": ctx}).status_code == 403
