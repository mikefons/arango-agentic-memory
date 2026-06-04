"""Unit tests for the in-process write queue (no container)."""

from __future__ import annotations

from arango_memory.ingest.queue import InProcessQueue, WriteIntent


def test_intent_key_is_deterministic_and_turn_sensitive() -> None:
    a = WriteIntent(content="x", tenant_id="t", agent_id="a", turn_index=0)
    b = WriteIntent(content="x", tenant_id="t", agent_id="a", turn_index=0)
    c = WriteIntent(content="x", tenant_id="t", agent_id="a", turn_index=1)
    assert a.key == b.key
    assert a.key != c.key


def test_retried_bumps_attempts_without_mutating() -> None:
    intent = WriteIntent(content="x", tenant_id="t", agent_id="a")
    assert intent.attempts == 0
    assert intent.retried().attempts == 1
    assert intent.attempts == 0  # frozen — original unchanged


def test_queue_is_fifo() -> None:
    queue = InProcessQueue()
    first = WriteIntent(content="a", tenant_id="t", agent_id="ag")
    second = WriteIntent(content="b", tenant_id="t", agent_id="ag")
    queue.enqueue(first)
    queue.enqueue(second)
    assert len(queue) == 2
    assert queue.pop() is first
    assert queue.pop() is second
    assert queue.pop() is None
    assert len(queue) == 0
