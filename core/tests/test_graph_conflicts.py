"""A write conflict in the graph pass is retried where it happens, so no entity link is lost.

Replaying the whole write would not do it: the stored turn then counts as a duplicate and
the graph pass is skipped.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from arango.aql import AQL
from arango.database import StandardDatabase

from arango_memory.client import ArangoMemoryClient
from arango_memory.ingest import conflicts, entities
from arango_memory.ingest.queue import InProcessQueue, WriteIntent
from arango_memory.ingest.store import StoreItem, store_many
from arango_memory.ingest.worker import WriteWorker

_TURNS = ["Alice met Bob at Acme.", "Carol joined Acme with Alice."]


class _Conflict(Exception):
    error_code = 1200  # ArangoDB write-write conflict


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conflicts, "_BASE_DELAY", 0.0)


def _conflict_on(monkeypatch: pytest.MonkeyPatch, statement: str, times: int) -> list[int]:
    """Make the first `times` runs of one AQL statement raise a write conflict."""
    real = AQL.execute
    raised: list[int] = []

    def execute(self: AQL, query: str, *args: Any, **kwargs: Any) -> Any:
        if query == statement and len(raised) < times:
            raised.append(1)
            raise _Conflict("write-write conflict")
        return real(self, query, *args, **kwargs)

    monkeypatch.setattr(AQL, "execute", execute)
    return raised


def _write(db: StandardDatabase, tenant: str, *, batch_size: int) -> None:
    queue = InProcessQueue()
    for i, text in enumerate(_TURNS):
        queue.enqueue(WriteIntent(content=text, tenant_id=tenant, agent_id="a", turn_index=i))
    WriteWorker(queue, db, backoff_base=0.0, batch_size=batch_size).drain()


def _graph(db: StandardDatabase, tenant: str) -> dict[str, Any]:
    def rows(query: str) -> list[Any]:
        return sorted(db.aql.execute(query, bind_vars={"t": tenant}))  # type: ignore[arg-type]

    return {
        "entities": rows("FOR e IN entities FILTER e.tenant_id == @t "
                         "RETURN CONCAT(e.name, ':', e.mention_count)"),
        "relations": rows("FOR r IN relates_to FILTER r.tenant_id == @t "
                          "RETURN CONCAT(DOCUMENT(r._from).name, '|', "
                          "DOCUMENT(r._to).name, ':', r.corroboration)"),
        "mentions": len(rows("FOR m IN mentions FILTER m.tenant_id == @t RETURN 1")),
        "produced_by": len(rows("FOR p IN produced_by FILTER p.tenant_id == @t RETURN 1")),
    }


def _normalised(graph: dict[str, Any]) -> dict[str, Any]:
    # An edge's direction follows entity keys, which differ between tenants.
    pairs = (r.rsplit(":", 1) for r in graph["relations"])
    return {**graph, "relations": sorted(
        f"{'|'.join(sorted(pair.split('|')))}:{n}" for pair, n in pairs)}


@pytest.mark.parametrize("batch_size,statement", [
    (8, entities._BULK_UPSERT_ENTITY),
    (8, entities._BULK_RELATE),
    (1, entities._UPSERT_ENTITY),
    (1, entities._RELATE),
], ids=["batch-upsert", "batch-relate", "single-upsert", "single-relate"])
def test_conflicting_graph_write_loses_and_doubles_nothing(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch, batch_size: int, statement: str
) -> None:
    _write(db, "clean", batch_size=batch_size)
    raised = _conflict_on(monkeypatch, statement, times=4)  # more than the batch reruns
    _write(db, "hit", batch_size=batch_size)

    assert len(raised) == 4
    assert _graph(db, "hit")["entities"]
    assert _normalised(_graph(db, "hit")) == _normalised(_graph(db, "clean"))


def test_gives_up_after_bounded_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def always() -> None:
        calls.append(1)
        raise _Conflict("write-write conflict")

    with pytest.raises(_Conflict):
        conflicts.retry_on_conflict(always)
    assert len(calls) == conflicts._ATTEMPTS


def test_other_errors_are_not_retried() -> None:
    calls: list[int] = []

    def broken() -> None:
        calls.append(1)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        conflicts.retry_on_conflict(broken)
    assert len(calls) == 1


def test_parallel_groups_sharing_entities_keep_exact_counts(
    db: StandardDatabase, client: ArangoMemoryClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real conflicts: several threads upsert the same entities at the same moment."""
    monkeypatch.setattr(conflicts, "_BASE_DELAY", 0.01)
    monkeypatch.setattr(conflicts, "_ATTEMPTS", 50)
    seen: list[int] = []
    real = conflicts.is_conflict

    def counting(exc: BaseException) -> bool:
        if real(exc):
            seen.append(1)
        return real(exc)

    monkeypatch.setattr(conflicts, "is_conflict", counting)
    writers, rounds = 4, 15
    barrier = threading.Barrier(writers, timeout=30)

    def write(writer: int) -> None:
        handle = ArangoMemoryClient(client.config).connect()
        for n in range(rounds):
            barrier.wait()
            store_many(
                handle,
                [StoreItem(content="Alice met Bob at Acme with Carol.", turn_index=n)],
                tenant_id="race", agent_id=f"w{writer}", extract=True,
            )

    with ThreadPoolExecutor(writers) as pool:
        list(pool.map(write, range(writers)))

    total = writers * rounds
    graph = _graph(db, "race")
    assert seen, "no conflict occurred, so this proved nothing"
    assert graph["entities"] == [f"{n}:{total}" for n in ("Acme", "Alice", "Bob", "Carol")]
    assert all(r.endswith(f":{total}") for r in graph["relations"])
    assert graph["mentions"] == graph["produced_by"] == total * 4
