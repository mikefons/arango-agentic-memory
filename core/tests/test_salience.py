"""Graph-algorithmic salience — PageRank centrality (DESIGN.md §9/§13)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from arango_memory.lifecycle.salience import pagerank


# ── pure PageRank ─────────────────────────────────────────
def test_pagerank_hub_is_most_central() -> None:
    nodes = ["hub", "a", "b", "c"]
    edges = [("hub", "a"), ("hub", "b"), ("hub", "c")]
    pr = pagerank(nodes, edges)
    assert pr["hub"] == 1.0  # normalized so the hub = 1.0
    assert pr["a"] < pr["hub"] and pr["b"] < pr["hub"]


def test_pagerank_empty_graph() -> None:
    assert pagerank([], []) == {}


# ── endpoint ──────────────────────────────────────────────
def test_salience_endpoint_writes_centrality(api: TestClient) -> None:
    ctx = {"tenant_id": "sal1", "agent_id": "a", "access_level": "write"}
    for other in ("Anna", "Bo", "Cy"):
        api.post("/v1/store", json={"content": f"Hub meets {other}", "ctx": ctx})

    graph: dict[str, list[dict[str, object]]] = {"nodes": [], "edges": []}
    for _ in range(20):
        graph = api.get("/v1/graph", params={"tenant_id": "sal1"}).json()
        if len(graph["nodes"]) >= 4 and graph["edges"]:
            break
        time.sleep(0.25)

    res = api.post("/v1/salience", json={"ctx": ctx})
    assert res.status_code == 200
    assert res.json()["entities"] >= 4

    nodes = api.get("/v1/graph", params={"tenant_id": "sal1"}).json()["nodes"]
    centrality = {n["name"]: n.get("centrality") for n in nodes}
    assert centrality.get("Hub") is not None
    # the well-connected hub is the most central
    assert centrality["Hub"] == max(v for v in centrality.values() if v is not None)


def test_salience_requires_write(api: TestClient) -> None:
    ctx = {"tenant_id": "sal2", "agent_id": "a", "access_level": "read"}
    assert api.post("/v1/salience", json={"ctx": ctx}).status_code == 403
