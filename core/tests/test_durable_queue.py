"""Durable ArangoDB-backed write queue (DESIGN.md §15, DQ-2)."""

from __future__ import annotations

import pytest
from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.api.app import create_app
from arango_memory.client import ArangoMemoryClient
from arango_memory.config import settings
from arango_memory.ingest.queue import ArangoQueue, WriteIntent
from arango_memory.ingest.worker import WriteWorker


def _intent(content: str, tenant: str) -> WriteIntent:
    return WriteIntent(content=content, tenant_id=tenant, agent_id="a")


def test_enqueue_claim_ack_roundtrip(db: StandardDatabase) -> None:
    q = ArangoQueue(db, lease_seconds=60)
    q.enqueue(_intent("alpha", "dq1"))
    assert len(q) == 1

    claim = q.claim()
    assert claim is not None
    assert claim.intent.content == "alpha"  # type: ignore[union-attr]
    assert len(q) == 0  # leased → no longer pending
    assert q.claim() is None  # nothing else claimable while leased

    q.ack(claim)
    # ack deletes the doc entirely
    assert db.collection("write_intents").count() == 0


def test_nack_releases_for_reclaim(db: StandardDatabase) -> None:
    q = ArangoQueue(db, lease_seconds=60)
    q.enqueue(_intent("beta", "dq2"))
    claim = q.claim()
    assert claim is not None and len(q) == 0

    q.nack(claim)  # release the lease
    assert len(q) == 1
    again = q.claim()
    assert again is not None and again.intent.content == "beta"  # type: ignore[union-attr]


def test_expired_lease_is_reclaimed_after_crash(db: StandardDatabase) -> None:
    # lease_seconds=0 → the lease is already expired the instant it's taken, modeling
    # a worker that claimed then crashed before ack. A fresh queue must redeliver it.
    q = ArangoQueue(db, lease_seconds=0)
    q.enqueue(_intent("gamma", "dq3"))
    first = q.claim()
    assert first is not None  # claimed (and "crashed" — never acked)

    survivor = ArangoQueue(db, lease_seconds=60)  # restart
    reclaimed = survivor.claim()
    assert reclaimed is not None and reclaimed.intent.content == "gamma"  # type: ignore[union-attr]


def test_worker_drains_the_durable_queue(db: StandardDatabase) -> None:
    q = ArangoQueue(db, lease_seconds=60)
    q.enqueue(_intent("Alice met Bob in Paris", "dq4"))
    worker = WriteWorker(q, db)

    assert worker.drain() == 1
    assert len(q) == 0
    assert db.collection("write_intents").count() == 0  # acked + removed
    # the intent committed to the graph
    n = next(db.aql.execute(
        "FOR m IN memories FILTER m.tenant_id == 'dq4' COLLECT WITH COUNT INTO c RETURN c"
    ))
    assert n >= 1


def test_fifo_order(db: StandardDatabase) -> None:
    q = ArangoQueue(db, lease_seconds=60)
    q.enqueue(_intent("first", "dq5"))
    q.enqueue(_intent("second", "dq5"))
    c1 = q.claim()
    c2 = q.claim()
    assert c1 is not None and c2 is not None
    assert c1.intent.content == "first"   # type: ignore[union-attr]
    assert c2.intent.content == "second"  # type: ignore[union-attr]


def test_create_app_selects_durable_backend(
    client: ArangoMemoryClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "write_queue_backend", "arango")
    with TestClient(create_app(client)) as api:
        assert isinstance(api.app.state.queue, ArangoQueue)  # type: ignore[attr-defined]
        ctx = {"tenant_id": "dq6", "agent_id": "a", "access_level": "write"}
        res = api.post("/v1/store", json={"content": "durable hello", "ctx": ctx})
        assert res.status_code == 200  # store enqueues onto the durable queue
