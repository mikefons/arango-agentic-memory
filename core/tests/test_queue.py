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


def test_claim_is_fifo_and_ack_removes() -> None:
    queue = InProcessQueue()
    first = WriteIntent(content="a", tenant_id="t", agent_id="ag")
    second = WriteIntent(content="b", tenant_id="t", agent_id="ag")
    queue.enqueue(first)
    queue.enqueue(second)
    assert len(queue) == 2  # pending excludes claimed

    c1 = queue.claim()
    assert c1 is not None and c1.intent is first
    assert len(queue) == 1  # claimed → no longer pending
    queue.ack(c1)

    c2 = queue.claim()
    assert c2 is not None and c2.intent is second
    queue.ack(c2)
    assert queue.claim() is None
    assert len(queue) == 0


def test_nack_returns_intent_to_the_front() -> None:
    queue = InProcessQueue()
    intent = WriteIntent(content="x", tenant_id="t", agent_id="ag")
    queue.enqueue(intent)

    claim = queue.claim()
    assert claim is not None and len(queue) == 0
    queue.nack(claim)  # release the lease
    assert len(queue) == 1
    again = queue.claim()
    assert again is not None and again.intent is intent  # redelivered


def test_ack_is_idempotent() -> None:
    queue = InProcessQueue()
    queue.enqueue(WriteIntent(content="x", tenant_id="t", agent_id="ag"))
    claim = queue.claim()
    assert claim is not None
    queue.ack(claim)
    queue.ack(claim)  # double-ack is a no-op, never raises
