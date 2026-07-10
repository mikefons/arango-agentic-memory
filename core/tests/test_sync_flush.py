"""Read-your-writes: synchronous store + `/v1/flush` barrier (MA-1, §15).

The value proposition is the *absence of sleeps* — every assertion here holds
immediately after the write call returns, which is what makes agent handoff
reliable. Contrast `test_api.py::test_store_then_retrieve_over_http`, which polls.
"""

from __future__ import annotations

import pytest
from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.api.app import get_queue_dep
from arango_memory.client import ArangoMemoryClient
from arango_memory.ingest.queue import InProcessQueue, WriteIntent
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import force_view_sync, retrieve
from arango_memory.schema.collections import DEAD_LETTER_COLLECTION


def test_force_view_sync_makes_write_immediately_visible(db: StandardDatabase) -> None:
    """The load-bearing mechanism: waitForSync forces the BM25 view to reflect a
    just-committed write with no polling. If this fails, the barrier needs a
    key-polling fallback instead."""
    store(db, content="the vault key is under the flagstone", tenant_id="t", agent_id="a")
    force_view_sync(db, "t")
    result = retrieve(db, query="where is the vault key", tenant_id="t", agent_id="a")
    assert result.hits, "waitForSync did not make the write visible to BM25"


def test_sync_store_is_immediately_retrievable(api: TestClient) -> None:
    ctx = {"tenant_id": "t_sync", "agent_id": "a"}
    resp = api.post(
        "/v1/store",
        json={"content": "sync marker zulu", "ctx": {**ctx, "access_level": "write"}, "sync": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "committed"
    # No retry loop: the write is guaranteed visible the instant store() returned.
    hits = api.post("/v1/retrieve", json={"query": "zulu", "ctx": ctx}).json()["hits"]
    assert hits, "sync store was not immediately retrievable"


def test_flush_barrier_waits_for_queued_writes(api: TestClient) -> None:
    ctx = {"tenant_id": "t_flush", "agent_id": "a"}
    api.post(
        "/v1/store",
        json={"content": "queued marker yankee", "ctx": {**ctx, "access_level": "write"}},
    )
    flushed = api.post("/v1/flush", json={"ctx": ctx, "timeout_ms": 5000})
    assert flushed.status_code == 200
    assert flushed.json()["status"] == "flushed"
    # After a successful flush the async write is visible with no further waiting.
    hits = api.post("/v1/retrieve", json={"query": "yankee", "ctx": ctx}).json()["hits"]
    assert hits, "flush returned before the queued write was retrievable"


def test_flush_timeout_returns_200_with_pending(api: TestClient) -> None:
    """A queue that never drains → status 'timeout' (still HTTP 200, caller branches)."""

    class StuckQueue:
        def pending_count(self, tenant_id: str) -> int:
            return 3

    api.app.dependency_overrides[get_queue_dep] = StuckQueue
    try:
        resp = api.post(
            "/v1/flush",
            json={"ctx": {"tenant_id": "t_stuck", "agent_id": "a"}, "timeout_ms": 100},
        )
    finally:
        api.app.dependency_overrides.pop(get_queue_dep, None)
    assert resp.status_code == 200
    assert resp.json() == {"status": "timeout", "pending": 3}


def test_pending_count_is_tenant_scoped() -> None:
    q = InProcessQueue()
    q.enqueue(WriteIntent(content="x", tenant_id="t1", agent_id="a"))
    q.enqueue(WriteIntent(content="y", tenant_id="t1", agent_id="a", turn_index=1))
    q.enqueue(WriteIntent(content="z", tenant_id="t2", agent_id="a"))
    assert q.pending_count("t1") == 2
    assert q.pending_count("t2") == 1
    assert q.pending_count("t3") == 0
    # In-flight (claimed, not acked) still counts — the barrier must not race a commit.
    claim = q.claim()
    assert claim is not None
    assert q.pending_count("t1") + q.pending_count("t2") == 3


def test_sync_store_is_idempotent(api: TestClient, client: ArangoMemoryClient) -> None:
    ctx = {"tenant_id": "t_idem", "agent_id": "a", "access_level": "write"}
    body = {"content": "idempotent marker", "ctx": ctx, "sync": True}
    api.post("/v1/store", json=body)
    api.post("/v1/store", json=body)  # same idempotency key → same _key
    memories = [m for m in client.db.collection("memories").all() if m["tenant_id"] == "t_idem"]
    assert len(memories) == 1, "identical sync stores must not duplicate the memory"


def test_sync_commit_failure_returns_503_and_does_not_dead_letter(
    api: TestClient, client: ArangoMemoryClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arango_memory.api import app as app_mod

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("commit exploded")

    monkeypatch.setattr(app_mod, "commit_intent", boom)
    resp = api.post(
        "/v1/store",
        json={
            "content": "will fail",
            "ctx": {"tenant_id": "t_fail", "agent_id": "a", "access_level": "write"},
            "sync": True,
        },
    )
    assert resp.status_code == 503
    # Sync failures surface to the caller; they do NOT dead-letter (that's the async path).
    dead = list(client.db.collection(DEAD_LETTER_COLLECTION).all())
    assert dead == [], "sync failure must not create a dead-letter record"
