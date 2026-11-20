"""Integration tests for the durable write worker (DESIGN.md §15)."""

from __future__ import annotations

import threading
import time

from arango.database import StandardDatabase
from pytest import MonkeyPatch

import arango_memory.ingest.worker as worker_mod
from arango_memory.client import ArangoMemoryClient
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


def _wait_until(predicate, timeout: float = 8.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_worker_thread_survives_a_queue_error_and_keeps_draining(db: StandardDatabase) -> None:
    class FlakyClaim(InProcessQueue):
        failed = False

        def claim_many(self, limit: int) -> list:  # type: ignore[type-arg, override]
            if not self.failed:
                self.failed = True
                raise RuntimeError("transient DB error while claiming")
            return super().claim_many(limit)

    queue = FlakyClaim()
    worker = WriteWorker(queue, db, backoff_base=0.0, batch_size=8)
    worker.start()
    try:
        _burst(queue, "t_survive", "a", 3)
        assert _wait_until(lambda: queue.pending_count("t_survive") == 0)
        assert worker.is_alive()  # the error did not kill the thread
        assert db.collection("memories").count() == 3
    finally:
        worker.stop()
    assert not worker.is_alive()


def test_a_failed_ack_releases_the_batch_and_it_is_committed_again(db: StandardDatabase) -> None:
    class FlakyAck(InProcessQueue):
        failed = False

        def ack_many(self, claims):  # type: ignore[no-untyped-def, override]
            if not self.failed:
                self.failed = True
                raise RuntimeError("transient DB error while acking")
            super().ack_many(claims)

    queue = FlakyAck()
    worker = WriteWorker(queue, db, backoff_base=0.0, batch_size=8)
    worker.start()
    try:
        _burst(queue, "t_ack", "a", 4)
        assert _wait_until(lambda: queue.pending_count("t_ack") == 0)
        assert worker.is_alive()
        # released, then committed again: idempotency keys mean no duplicates
        assert db.collection("memories").count() == 4
        assert db.collection(DEAD_LETTER_COLLECTION).count() == 0
    finally:
        worker.stop()



# ── Worker pool (WQ-1a): agent groups commit in parallel ─────────────────────────────

def _three_agents(queue: InProcessQueue, tenant: str) -> None:
    for agent in ("fin", "leg", "tech"):
        _burst(queue, tenant, agent, 3)


def test_agent_groups_commit_at_the_same_time_each_on_its_own_thread(
    db: StandardDatabase, monkeypatch: MonkeyPatch
) -> None:
    barrier = threading.Barrier(3, timeout=5)  # only passes if all three calls overlap
    seen: list[tuple[str, str, list[int]]] = []
    real = worker_mod.store_many

    def overlapping(db_: StandardDatabase, items: list, **kw: object) -> object:  # type: ignore[type-arg]
        agent = str(kw["agent_id"])
        if agent not in {a for a, _, _ in seen}:  # a conflict retry must not wait again
            barrier.wait()
            seen.append((agent, threading.current_thread().name, [i.turn_index for i in items]))
        return real(db_, items, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_mod, "store_many", overlapping)
    queue = InProcessQueue()
    worker = WriteWorker(queue, db, backoff_base=0.0, batch_size=16, concurrency=3)
    _three_agents(queue, "t_pool")
    try:
        assert worker.drain() == 9
    finally:
        worker.stop()

    assert not barrier.broken
    assert len({thread for _, thread, _ in seen}) == 3
    assert all(turns == [0, 1, 2] for _, _, turns in seen)  # each agent's order is kept
    assert db.collection("memories").count() == 9
    assert db.collection(DEAD_LETTER_COLLECTION).count() == 0
    assert queue.pending_count("t_pool") == 0


def test_concurrency_one_commits_groups_one_after_another(
    db: StandardDatabase, monkeypatch: MonkeyPatch
) -> None:
    threads: set[str] = set()
    real = worker_mod.store_many

    def spy(db_: StandardDatabase, items: list, **kw: object) -> object:  # type: ignore[type-arg]
        threads.add(threading.current_thread().name)
        return real(db_, items, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_mod, "store_many", spy)
    queue = InProcessQueue()
    worker = WriteWorker(queue, db, backoff_base=0.0, batch_size=16, concurrency=1)
    _three_agents(queue, "t_serial")
    worker.drain()
    assert threads == {threading.current_thread().name}
    assert worker._pool is None  # nothing was spawned


def test_parallel_groups_that_share_entities_commit_what_a_serial_run_commits(
    db: StandardDatabase, client: ArangoMemoryClient
) -> None:
    serial, pooled = InProcessQueue(), InProcessQueue()
    for q, tenant in ((serial, "t_ps"), (pooled, "t_pp")):
        for agent in ("a1", "a2", "a3", "a4"):
            _burst(q, tenant, agent, 6)  # every agent mentions Alice and Bob
    WriteWorker(serial, db, backoff_base=0.0, batch_size=32, concurrency=1).drain()
    worker = WriteWorker(
        pooled, db, backoff_base=0.0, batch_size=32, concurrency=4,
        db_factory=lambda: ArangoMemoryClient(client.config).connect(),
    )
    try:
        worker.drain()
    finally:
        worker.stop()

    def count(coll: str, tenant: str) -> int:
        return int(next(iter(db.aql.execute(
            f"RETURN LENGTH(FOR d IN {coll} FILTER d.tenant_id == @t RETURN 1)",
            bind_vars={"t": tenant},
        ))))

    for coll in ("episodes", "memories", "entities"):
        assert count(coll, "t_ps") == count(coll, "t_pp"), coll
    assert count("memories", "t_pp") == 24
    assert db.collection(DEAD_LETTER_COLLECTION).count() == 0
    assert pooled.pending_count("t_pp") == 0


class _Conflict(Exception):
    error_code = 1200  # ArangoDB write-write conflict


def test_a_conflict_inside_a_batch_is_retried_not_sent_to_the_per_intent_path(
    db: StandardDatabase, monkeypatch: MonkeyPatch
) -> None:
    attempts = {"n": 0}
    real = worker_mod.store_many

    def conflicting(db_: StandardDatabase, items: list, **kw: object) -> object:  # type: ignore[type-arg]
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise _Conflict("write-write conflict")
        return real(db_, items, **kw)  # type: ignore[arg-type]

    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("fell back to per-intent store()")

    monkeypatch.setattr(worker_mod, "store_many", conflicting)
    monkeypatch.setattr(worker_mod, "store", never)
    queue = InProcessQueue()
    worker = WriteWorker(queue, db, backoff_base=0.0, batch_size=8, concurrency=2)
    _burst(queue, "t_cf", "a", 3)
    worker.drain()
    assert attempts["n"] == 3
    assert db.collection("memories").count() == 3


def _store_down(*args: object, **kwargs: object) -> None:
    raise RuntimeError("x")


def test_a_failing_group_waits_for_the_others_then_releases_the_round(
    db: StandardDatabase, monkeypatch: MonkeyPatch
) -> None:
    real = worker_mod.store_many
    done = threading.Event()

    def slow_good_fast_bad(db_: StandardDatabase, items: list, **kw: object) -> object:  # type: ignore[type-arg]
        if kw["agent_id"] == "bad":
            raise RuntimeError("batch down")
        time.sleep(0.2)
        out = real(db_, items, **kw)  # type: ignore[arg-type]
        done.set()
        return out

    def dead_letter_down(self: WriteWorker, *a: object) -> None:
        raise RuntimeError("cannot dead-letter")

    monkeypatch.setattr(worker_mod, "store_many", slow_good_fast_bad)
    monkeypatch.setattr(worker_mod, "store", _store_down)
    monkeypatch.setattr(WriteWorker, "_dead_letter", dead_letter_down)
    queue = InProcessQueue()
    worker = WriteWorker(queue, db, backoff_base=0.0, max_retries=1, batch_size=8, concurrency=2)
    _burst(queue, "t_fail", "good", 2)
    _burst(queue, "t_fail", "bad", 2)
    try:
        try:
            worker.drain()
            raised = False
        except RuntimeError:
            raised = True
    finally:
        worker.stop()
    assert raised
    assert done.is_set()  # the healthy group finished before the error surfaced
    assert db.collection("memories").count() == 2
