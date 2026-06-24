"""HTTP contract tests for the core API — the TS↔Python seam (DESIGN.md §19, §22).

Exercises the FastAPI app built via `create_app(client)` against a real
container, pinning the request/response shapes the Vercel adapter depends on.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from arango_memory.client import ArangoMemoryClient


def test_health(api: TestClient) -> None:
    resp = api.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["arango"] is True
    assert body["mode"] == "lite"
    assert isinstance(body["latency_ms"], dict)  # process-global p50/p95/p99 (§23)


def test_ready_200_when_db_reachable(api: TestClient) -> None:
    resp = api.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "arango": True}


def test_ready_503_when_db_unreachable(
    api: TestClient, client: ArangoMemoryClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Readiness must fail (503) when the DB is unreachable, so orchestrators stop
    # routing — without restarting the pod (that's /health's job, which stays 200).
    monkeypatch.setattr(client, "ping", lambda: False)
    resp = api.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "unavailable"
    assert api.get("/health").status_code == 200  # liveness unaffected by the DB


def test_openapi_version_tracks_package(api: TestClient) -> None:
    from arango_memory import __version__

    assert __version__ != "0.0.0+unknown"  # installed → metadata resolved (single source)
    body = api.get("/openapi.json").json()
    assert body["info"]["version"] == __version__


def test_openapi_groups_routes_by_tag(api: TestClient) -> None:
    spec = api.get("/openapi.json").json()
    tags = {t["name"] for t in spec.get("tags", [])}
    assert {"ingestion", "retrieval", "lifecycle"} <= tags  # grouped for /docs
    # the store route is tagged so it lands under its group, not "default"
    assert spec["paths"]["/v1/store"]["post"]["tags"] == ["ingestion"]


def test_docs_are_reachable(api: TestClient) -> None:
    assert api.get("/docs").status_code == 200       # Swagger UI
    assert api.get("/redoc").status_code == 200       # ReDoc


def test_store_response_shape(api: TestClient) -> None:
    resp = api.post(
        "/v1/store",
        json={
            "content": "alpha bravo charlie",
            "ctx": {"tenant_id": "t1", "agent_id": "a1", "access_level": "write"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"          # durable write path (§15)
    assert body["episode_id"]                  # deterministic from the idempotency key
    assert body["memory_ids"] == [f"{body['episode_id']}-mem"]


def test_store_then_retrieve_over_http(api: TestClient) -> None:
    ctx = {"tenant_id": "t_http", "agent_id": "a_http"}
    write_ctx = {**ctx, "access_level": "write"}
    api.post("/v1/store", json={"content": "delta echo foxtrot", "ctx": write_ctx})

    body: dict[str, object] = {}
    for _ in range(20):
        resp = api.post("/v1/retrieve", json={"query": "echo foxtrot", "ctx": ctx})
        assert resp.status_code == 200
        body = resp.json()
        if body["hits"]:
            break
        time.sleep(0.25)

    assert body["hits"], "stored memory not retrievable over HTTP"
    hit = body["hits"][0]  # type: ignore[index]
    assert set(hit.keys()) == {"text", "score", "source"}
    assert body["tokens_injected"] > 0
    assert isinstance(body["context"], str)


def test_retrieve_empty_for_unknown_tenant(api: TestClient) -> None:
    resp = api.post(
        "/v1/retrieve",
        json={"query": "anything", "ctx": {"tenant_id": "ghost", "agent_id": "nobody"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["hits"] == []
    assert body["context"] == ""
    assert body["tokens_injected"] == 0


def test_store_validation_error(api: TestClient) -> None:
    # Missing required `content` → 422 from FastAPI validation.
    resp = api.post("/v1/store", json={"ctx": {"tenant_id": "t1", "agent_id": "a1"}})
    assert resp.status_code == 422


def test_step_queue_then_lookup(api: TestClient) -> None:
    ctx = {"tenant_id": "t_step_api", "agent_id": "a", "access_level": "write"}
    resp = api.post(
        "/v1/step",
        json={"tool_name": "search", "arguments": {"q": "x"}, "outcome": "success", "ctx": ctx},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["step_id"]

    steps: list[dict[str, object]] = []
    for _ in range(20):
        got = api.get("/v1/steps", params={"tenant_id": "t_step_api", "agent_id": "a"})
        assert got.status_code == 200
        steps = got.json()["steps"]
        if steps:
            break
        time.sleep(0.25)

    assert len(steps) == 1
    assert steps[0]["tool_name"] == "search"
    assert steps[0]["use_count"] == 1
