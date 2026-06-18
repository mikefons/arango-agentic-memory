"""Concurrency + multi-tenant isolation under load (DESIGN.md §22).

Exercises the real DB (testcontainers) with several threads writing/reading at
once, asserting (a) tenant scoping holds — a tenant never sees another tenant's
data — and (b) concurrent durable-queue workers never double-process an intent
(the exclusive-locked `claim` lease, §15). Each thread uses its own connection
(python-arango sessions aren't meant to be shared across threads).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from arango.database import StandardDatabase

from arango_memory.client import ArangoMemoryClient
from arango_memory.config import Settings
from arango_memory.ingest.queue import ArangoQueue, Claim, WriteIntent
from arango_memory.ingest.store import store
from arango_memory.ingest.worker import WriteWorker
from arango_memory.retrieve.search import RetrieveResult, retrieve

_TENANTS = 4
_PER_TENANT = 5


def _connect(settings: Settings) -> StandardDatabase:
    """A fresh connection to the per-test DB for one thread (DB already created)."""
    return ArangoMemoryClient(config=settings).connect()


def test_concurrent_writes_isolate_by_tenant(
    db: StandardDatabase,
    settings: Settings,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    # Each tenant gets a unique marker token; if scoping leaked, a query for one
    # tenant's marker under another tenant would surface it through the shared view.
    markers = {f"ten_{i}": f"zephyrmark{i}" for i in range(_TENANTS)}

    def writes(tenant: str, marker: str) -> None:
        conn = _connect(settings)
        for turn in range(_PER_TENANT):
            store(conn, content=f"{marker} note {turn}", tenant_id=tenant,
                  agent_id="a", turn_index=turn)

    with ThreadPoolExecutor(max_workers=_TENANTS) as pool:
        list(pool.map(lambda kv: writes(*kv), markers.items()))

    # Each tenant wrote exactly _PER_TENANT memories — no lost or cross-tenant writes.
    for tenant in markers:
        count = next(db.aql.execute(
            "FOR m IN memories FILTER m.tenant_id == @t COLLECT WITH COUNT INTO c RETURN c",
            bind_vars={"t": tenant},
        ))
        assert count == _PER_TENANT, f"{tenant} sees {count} memories, expected {_PER_TENANT}"

    # Isolation: a tenant's marker is retrievable under that tenant only.
    for tenant, marker in markers.items():
        own = wait_for_searchable(db, query=marker, tenant_id=tenant, agent_id="a")
        assert own.hits, f"{tenant} cannot retrieve its own write"
        for other in markers:
            if other == tenant:
                continue
            leaked = retrieve(db, query=marker, tenant_id=other, agent_id="a")
            assert all(marker not in h.text for h in leaked.hits), (
                f"{tenant}'s data ({marker}) leaked into {other}'s retrieval"
            )


def test_concurrent_workers_never_double_process(
    db: StandardDatabase, settings: Settings
) -> None:
    # Seed the durable queue, then drain it from several worker threads at once.
    # The exclusive-locked claim must hand each intent to exactly one worker.
    n_intents = 24
    q = ArangoQueue(db, lease_seconds=60)
    for i in range(n_intents):
        q.enqueue(WriteIntent(content=f"intent number {i}", tenant_id="conc_w", agent_id="a"))

    processed: list[str] = []
    lock = threading.Lock()

    class _RecordingWorker(WriteWorker):
        def process(self, intent: object) -> bool:  # type: ignore[override]
            with lock:
                processed.append(intent.key)  # type: ignore[attr-defined]
            return super().process(intent)  # type: ignore[arg-type]

    def drain() -> None:
        worker = _RecordingWorker(q, _connect(settings))
        while (claim := q.claim()) is not None:
            assert isinstance(claim, Claim)
            worker.process(claim.intent)
            q.ack(claim)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for fut in [pool.submit(drain) for _ in range(4)]:
            fut.result()

    # Every intent processed exactly once — no duplicates from a double-claim.
    assert len(processed) == n_intents
    assert len(set(processed)) == n_intents
    assert len(q) == 0
    assert db.collection("write_intents").count() == 0
