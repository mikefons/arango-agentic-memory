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
