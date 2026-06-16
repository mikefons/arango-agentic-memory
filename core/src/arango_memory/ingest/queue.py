"""Durable write queue (DESIGN.md §15).

The adapter enqueues an idempotency-keyed write intent and returns immediately;
a worker **claims** an intent, commits it to ArangoDB, then **acks** it (see
`worker.py`). The claim→ack contract (not a destructive `pop`) is what makes
durability possible: a crash between claim and ack leaves the intent leased, so a
durable backend can redeliver it — commits are idempotency-keyed, so at-least-once
delivery never duplicates. The `WriteQueue` Protocol is the seam a durable backend
(ArangoDB, then Redis/SQS) slots into without touching callers; `InProcessQueue`
is the in-memory default (fast, zero-config; loses unacked work on process death).
"""

from __future__ import annotations

import hashlib
import itertools
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

from ..models import idempotency_key


@dataclass(frozen=True)
class WriteIntent:
    """One queued store request. Carries everything `store()` needs to commit."""

    content: str
    tenant_id: str
    agent_id: str
    session_id: str | None = None
    turn_index: int = 0
    source_reliability: float = 1.0
    memory_type: str = "episodic"
    attempts: int = 0

    @property
    def key(self) -> str:
        return idempotency_key(
            tenant_id=self.tenant_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            content=self.content,
            turn_index=self.turn_index,
        )

    def retried(self) -> WriteIntent:
        """A copy with the attempt counter bumped."""
        return WriteIntent(
            content=self.content,
            tenant_id=self.tenant_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            turn_index=self.turn_index,
            source_reliability=self.source_reliability,
            memory_type=self.memory_type,
            attempts=self.attempts + 1,
        )


@dataclass(frozen=True)
class StepIntent:
    """A queued procedural-memory (tool-trace) write (DESIGN.md §5, §11)."""

    tool_name: str
    arguments: dict[str, Any]
    outcome: str
    tenant_id: str
    agent_id: str
    pattern_summary: str = ""
    source_memory_key: str | None = None
    prev_step_key: str | None = None

    @property
    def key(self) -> str:
        raw = f"{self.tenant_id}\x1f{self.agent_id}\x1f{self.tool_name}\x1f{self.outcome}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


Intent = WriteIntent | StepIntent


@dataclass(frozen=True)
class Claim:
    """A leased intent: the work plus an opaque `handle` the backend acks/nacks by."""

    intent: Intent
    handle: str


class WriteQueue(Protocol):
    def enqueue(self, intent: Intent) -> None: ...
    def claim(self) -> Claim | None: ...
    def ack(self, claim: Claim) -> None: ...
    def nack(self, claim: Claim) -> None: ...
    def __len__(self) -> int: ...  # pending (unclaimed) intents


class InProcessQueue:
    """Thread-safe FIFO queue with in-memory leasing (claim → ack/nack)."""

    def __init__(self) -> None:
        self._pending: deque[Intent] = deque()
        self._inflight: dict[str, Intent] = {}
        self._handles = itertools.count()
        self._lock = threading.Lock()

    def enqueue(self, intent: Intent) -> None:
        with self._lock:
            self._pending.append(intent)

    def claim(self) -> Claim | None:
        with self._lock:
            if not self._pending:
                return None
            intent = self._pending.popleft()
            handle = str(next(self._handles))
            self._inflight[handle] = intent
            return Claim(intent=intent, handle=handle)

    def ack(self, claim: Claim) -> None:
        with self._lock:
            self._inflight.pop(claim.handle, None)

    def nack(self, claim: Claim) -> None:
        """Release a leased intent back to the front of the queue (e.g. on shutdown)."""
        with self._lock:
            if self._inflight.pop(claim.handle, None) is not None:
                self._pending.appendleft(claim.intent)

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)
