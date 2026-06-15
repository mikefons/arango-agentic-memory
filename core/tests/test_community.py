"""Graph community detection — label propagation (DESIGN.md §9/§13)."""

from __future__ import annotations

import time

from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.generation import FakeGenerator
from arango_memory.ingest.store import store
from arango_memory.lifecycle.community import label_propagation
from arango_memory.lifecycle.dream import run_dream_state


# ── pure LPA ──────────────────────────────────────────────
def test_lpa_separates_disconnected_clusters() -> None:
    # Two triangles with no edge between them → two distinct communities.
    nodes = ["a1", "a2", "a3", "b1", "b2", "b3"]
    edges = [
        ("a1", "a2"), ("a2", "a3"), ("a1", "a3"),
        ("b1", "b2"), ("b2", "b3"), ("b1", "b3"),
    ]
    labels = label_propagation(nodes, edges)
    assert labels["a1"] == labels["a2"] == labels["a3"]
    assert labels["b1"] == labels["b2"] == labels["b3"]
    assert labels["a1"] != labels["b1"]
    assert set(labels.values()) == {0, 1}  # dense ids


def test_lpa_is_deterministic() -> None:
    nodes = ["a1", "a2", "b1", "b2"]
    edges = [("a1", "a2"), ("b1", "b2")]
    assert label_propagation(nodes, edges) == label_propagation(nodes, edges)


def test_lpa_empty_and_isolated() -> None:
    assert label_propagation([], []) == {}
    # Two isolated nodes → two singleton communities.
    assert set(label_propagation(["x", "y"], []).values()) == {0, 1}


def test_lpa_largest_community_is_zero() -> None:
    nodes = ["a1", "a2", "a3", "b1"]
    edges = [("a1", "a2"), ("a2", "a3"), ("a1", "a3")]  # b1 isolated
    labels = label_propagation(nodes, edges)
    assert labels["a1"] == labels["a2"] == labels["a3"] == 0  # biggest → 0
    assert labels["b1"] == 1


# ── endpoint ──────────────────────────────────────────────
def test_community_endpoint_writes_labels(api: TestClient) -> None:
    ctx = {"tenant_id": "com1", "agent_id": "a", "access_level": "write"}
    for other in ("Anna", "Bo", "Cy"):
        api.post("/v1/store", json={"content": f"Hub meets {other}", "ctx": ctx})

    for _ in range(20):
        graph = api.get("/v1/graph", params={"tenant_id": "com1"}).json()
        if len(graph["nodes"]) >= 4 and graph["edges"]:
            break
        time.sleep(0.25)

    res = api.post("/v1/community", json={"ctx": ctx})
    assert res.status_code == 200
    body = res.json()
    assert body["entities"] >= 4
    assert body["communities"] >= 1

    nodes = api.get("/v1/graph", params={"tenant_id": "com1"}).json()["nodes"]
    assert all(n.get("community") is not None for n in nodes)


def test_community_requires_write(api: TestClient) -> None:
    ctx = {"tenant_id": "com2", "agent_id": "a", "access_level": "read"}
    assert api.post("/v1/community", json={"ctx": ctx}).status_code == 403


# ── Dream State scoping ───────────────────────────────────
def _gen(conflict: str = "CONTRADICTS") -> FakeGenerator:
    def handler(prompt: str, system: str | None) -> str:
        return conflict if system and "DISTINCT" in system else ""

    return FakeGenerator(handler=handler)


def _key_of(db: StandardDatabase, tenant: str, name: str) -> str:
    return next(
        db.aql.execute(
            "FOR e IN entities FILTER e.tenant_id == @t AND e.name == @n RETURN e._key",
            bind_vars={"t": tenant, "n": name},
        )
    )


def test_different_community_skips_supersede(db: StandardDatabase) -> None:
    t = "t_com_dream"
    store(db, content="Acme Corp and Globex Inc", tenant_id=t, agent_id="a")
    acme, globex = _key_of(db, t, "Acme Corp"), _key_of(db, t, "Globex Inc")
    entities = db.collection("entities")
    entities.update({"_key": acme, "needs_review": True, "conflict_with": globex})
    # Put them in distinct communities → the conflict confirm must be skipped.
    entities.update({"_key": acme, "community": 0})
    entities.update({"_key": globex, "community": 1})

    result = run_dream_state(db, tenant_id=t, generator=_gen("CONTRADICTS"), breaker_threshold=1.0)
    assert result.superseded == 0
    assert entities.get(globex)["invalid_at"] is None  # untouched
    assert entities.get(acme)["needs_review"] is True   # still flagged for a later run


def test_same_community_still_supersedes(db: StandardDatabase) -> None:
    t = "t_com_dream2"
    store(db, content="Acme Corp and Globex Inc", tenant_id=t, agent_id="a")
    acme, globex = _key_of(db, t, "Acme Corp"), _key_of(db, t, "Globex Inc")
    entities = db.collection("entities")
    entities.update({"_key": acme, "needs_review": True, "conflict_with": globex})
    entities.update({"_key": acme, "community": 0})
    entities.update({"_key": globex, "community": 0})  # same community

    result = run_dream_state(db, tenant_id=t, generator=_gen("CONTRADICTS"), breaker_threshold=1.0)
    assert result.superseded == 1
    assert entities.get(globex)["invalid_at"] is not None
