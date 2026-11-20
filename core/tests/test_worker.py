"""Integration tests for the durable write worker (DESIGN.md §15)."""

from __future__ import annotations

from arango.database import StandardDatabase
from pytest import MonkeyPatch

import arango_memory.ingest.worker as worker_mod
from arango_memory.ingest.queue import InProcessQueue, WriteIntent
from arango_memory.ingest.worker import WriteWorker
from arango_memory.schema.collections import DEAD_LETTER_COLLECTION


def test_drain_commits_episode_memory_and_entities(db: StandardDatabase) -> None:
    queue = InProcessQueue()
    worker = WriteWorker(queue, db, backoff_base=0.0)
    queue.enqueue(WriteIntent(content="Alice met Bob", tenant_id="t_w", agent_id="a"))

    assert worker.drain() == 1
    assert db.collection("episodes").count() == 1
    assert db.collection("memories").count() == 1
    assert db.collection("entities").count() == 2  # Alice, Bob


def test_retries_then_succeeds_without_dead_letter(
    db: StandardDatabase, monkeypatch: MonkeyPatch
) -> None:
    calls = {"n": 0}

    def flaky(*args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")

    monkeypatch.setattr(worker_mod, "store", flaky)
    queue = InProcessQueue()
    worker = WriteWorker(queue, db, max_retries=3, backoff_base=0.0)

    queue.enqueue(WriteIntent(content="x", tenant_id="t_r", agent_id="a"))
    assert worker.drain() == 1
    assert calls["n"] == 2  # failed once, succeeded on retry
    assert db.collection(DEAD_LETTER_COLLECTION).count() == 0


def test_dead_letters_on_persistent_failure(
    db: StandardDatabase, monkeypatch: MonkeyPatch
) -> None:
    def always_fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_mod, "store", always_fail)
    queue = InProcessQueue()
    worker = WriteWorker(queue, db, max_retries=2, backoff_base=0.0)

    intent = WriteIntent(content="x", tenant_id="t_d", agent_id="a", turn_index=5)
    queue.enqueue(intent)
    worker.drain()

    dead = db.collection(DEAD_LETTER_COLLECTION)
    assert dead.count() == 1
    doc = dead.get(intent.key)
    assert doc["intent"]["content"] == "x"
    assert doc["attempts"] == 2
    assert "boom" in doc["error"]


def test_replay_failed_reenqueues_and_clears(
    db: StandardDatabase, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_mod, "store", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    queue = InProcessQueue()
    worker = WriteWorker(queue, db, max_retries=1, backoff_base=0.0)

    queue.enqueue(WriteIntent(content="x", tenant_id="t_rp", agent_id="a"))
    worker.drain()
    assert db.collection(DEAD_LETTER_COLLECTION).count() == 1
    assert len(queue) == 0

    assert worker.replay_failed() == 1
    assert len(queue) == 1
    assert db.collection(DEAD_LETTER_COLLECTION).count() == 0


def _burst(queue: InProcessQueue, tenant: str, agent: str, n: int) -> None:
    for i in range(n):
        queue.enqueue(
            WriteIntent(
                content=f"Alice met Bob number {i}", tenant_id=tenant, agent_id=agent, turn_index=i
            )
        )


def test_batch_commit_uses_one_store_many_call_per_agent(
    db: StandardDatabase, monkeypatch: MonkeyPatch
) -> None:
    calls: list[tuple[str, str, int]] = []
    real = worker_mod.store_many

    def spy(db_: StandardDatabase, items: list, **kw: object) -> object:  # type: ignore[type-arg]
        calls.append((str(kw["tenant_id"]), str(kw["agent_id"]), len(items)))
        return real(db_, items, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_mod, "store_many", spy)
    queue = InProcessQueue()
    worker = WriteWorker(queue, db, backoff_base=0.0, batch_size=8)
    _burst(queue, "t_b", "fin", 4)
    _burst(queue, "t_b", "leg", 3)

    assert worker.drain() == 7
    assert sorted(calls) == [("t_b", "fin", 4), ("t_b", "leg", 3)]
    assert db.collection("episodes").count() == 7
    assert db.collection("memories").count() == 7
    assert db.collection("entities").count() >= 2  # Alice, Bob (merged across the batch)
    assert queue.pending_count("t_b") == 0


def test_batch_commit_matches_one_by_one_records(db: StandardDatabase) -> None:
    one, many = InProcessQueue(), InProcessQueue()
    _burst(one, "t_one", "a", 6)
    _burst(many, "t_many", "a", 6)
    WriteWorker(one, db, backoff_base=0.0, batch_size=1).drain()
    WriteWorker(many, db, backoff_base=0.0, batch_size=6).drain()

    def count(coll: str, tenant: str) -> int:
        return int(next(iter(db.aql.execute(
            f"RETURN LENGTH(FOR d IN {coll} FILTER d.tenant_id == @t RETURN 1)",
            bind_vars={"t": tenant},
        ))))

    for coll in ("episodes", "memories", "entities"):
        assert count(coll, "t_one") == count(coll, "t_many"), coll


def test_failed_batch_falls_back_per_intent_and_commits_the_rest(
    db: StandardDatabase, monkeypatch: MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("batch down")

    monkeypatch.setattr(worker_mod, "store_many", boom)
    queue = InProcessQueue()
    worker = WriteWorker(queue, db, backoff_base=0.0, batch_size=8)
    _burst(queue, "t_fb", "a", 4)

    assert worker.drain() == 4
    assert db.collection("memories").count() == 4  # committed one by one via store()
    assert db.collection(DEAD_LETTER_COLLECTION).count() == 0
    assert queue.pending_count("t_fb") == 0


def test_session_bound_and_working_intents_stay_on_the_per_intent_path(
    db: StandardDatabase, monkeypatch: MonkeyPatch
) -> None:
    batched: list[int] = []
    real = worker_mod.store_many

    def spy(db_: StandardDatabase, items: list, **kw: object) -> object:  # type: ignore[type-arg]
        batched.append(len(items))
        return real(db_, items, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_mod, "store_many", spy)
    queue = InProcessQueue()
    worker = WriteWorker(queue, db, backoff_base=0.0, batch_size=8)
    for i in range(3):
        queue.enqueue(WriteIntent(content=f"s{i}", tenant_id="t_s", agent_id="a",
                                  session_id="sess", turn_index=i))
        queue.enqueue(WriteIntent(content=f"w{i}", tenant_id="t_s", agent_id="a",
                                  memory_type="working", turn_index=10 + i))

    assert worker.drain() == 6
    assert batched == []  # none qualified for the bulk path
    assert db.collection("memories").count() == 6


def test_batch_size_one_never_calls_claim_many(db: StandardDatabase) -> None:
    class Spy(InProcessQueue):
        def claim_many(self, limit: int) -> list:  # type: ignore[type-arg, override]
            raise AssertionError("claim_many must not be used at batch_size=1")

    queue = Spy()
    _burst(queue, "t_one_only", "a", 2)
    assert WriteWorker(queue, db, backoff_base=0.0, batch_size=1).drain() == 2

