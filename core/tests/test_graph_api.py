"""Full semantic-graph read for visualization (DESIGN.md §11, §19)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _graph(api: TestClient, tenant: str) -> dict[str, list[dict[str, object]]]:
    return api.get("/v1/graph", params={"tenant_id": tenant}).json()


def test_graph_returns_entities_and_relates_to(api: TestClient) -> None:
    ctx = {"tenant_id": "g1", "agent_id": "a", "access_level": "write"}
    api.post("/v1/store", json={"content": "Alice met Bob", "ctx": ctx})

    graph: dict[str, list[dict[str, object]]] = {"nodes": [], "edges": []}
    for _ in range(20):
        graph = _graph(api, "g1")
        if graph["nodes"] and graph["edges"]:
            break
        time.sleep(0.25)

    names = {n["name"] for n in graph["nodes"]}
    assert {"Alice", "Bob"} <= names
    assert any(e["kind"] == "relates_to" for e in graph["edges"])
    assert all("embedding" not in n for n in graph["nodes"])  # §17


def test_graph_includes_superseded_nodes_and_edges(api: TestClient) -> None:
    ctx = {"tenant_id": "g2", "agent_id": "a", "access_level": "write"}
    seeded = api.post(
        "/v1/seed", json={"profile": {"preferences": ["Old Tale", "True Tale"]}, "ctx": ctx}
    ).json()
    old_key, new_key = seeded["entity_ids"][0], seeded["entity_ids"][1]
    api.post("/v1/supersede", json={"new_key": new_key, "old_key": old_key, "ctx": ctx})

    graph = _graph(api, "g2")
    # superseded entity is still present (with invalid_at) — unlike /v1/entities
    superseded = [n for n in graph["nodes"] if n["name"] == "Old Tale"]
    assert superseded and superseded[0]["invalid_at"] is not None
    assert any(e["kind"] == "supersedes" for e in graph["edges"])


def test_graph_is_tenant_scoped(api: TestClient) -> None:
    ctx = {"tenant_id": "g3", "agent_id": "a", "access_level": "write"}
    api.post("/v1/store", json={"content": "Charlie waved", "ctx": ctx})
    assert _graph(api, "other-tenant") == {"nodes": [], "edges": []}
