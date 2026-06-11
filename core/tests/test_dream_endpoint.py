"""Dream State consolidation endpoint (DESIGN.md §13)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _alice_mentions(api: TestClient, tenant: str) -> int:
    rows = api.get("/v1/entities", params={"tenant_id": tenant}).json()["entities"]
    alice = [e for e in rows if e["name"] == "Alice"]
    return int(alice[0]["mention_count"]) if alice else 0


def test_dream_reviews_well_attested_entities(api: TestClient) -> None:
    ctx = {"tenant_id": "dr1", "agent_id": "a", "access_level": "write"}
    # six distinct episodes mentioning Alice → mention_count climbs past the
    # consolidation threshold, making her a Dream State candidate.
    for i in range(6):
        api.post("/v1/store", json={"content": f"Alice did thing number {i}", "ctx": ctx})

    for _ in range(30):
        if _alice_mentions(api, "dr1") >= 5:
            break
        time.sleep(0.25)

    res = api.post("/v1/dream", json={"ctx": ctx})
    assert res.status_code == 200
    body = res.json()
    assert body["reviewed"] >= 1
    assert body["breaker_tripped"] is False


def test_dream_requires_write(api: TestClient) -> None:
    ctx = {"tenant_id": "dr2", "agent_id": "a", "access_level": "read"}
    assert api.post("/v1/dream", json={"ctx": ctx}).status_code == 403
