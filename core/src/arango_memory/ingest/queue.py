"""Durable write queue (DESIGN.md §15).

The adapter enqueues an idempotency-keyed write intent and returns immediately;
a worker drains the queue and commits to ArangoDB (see `worker.py`). The queue
is in-process for now — the `WriteQueue` Protocol is the seam a durable backend
(Redis/SQS) slots into later without touching callers.
"""

from __future__ import annotations

import hashlib
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


class WriteQueue(Protocol):
    def enqueue(self, intent: Intent) -> None: ...
    def pop(self) -> Intent | None: ...
    def __len__(self) -> int: ...


class InProcessQueue:
    """Thread-safe FIFO queue backed by a deque."""

    def __init__(self) -> None:
        self._items: deque[Intent] = deque()
        self._lock = threading.Lock()

    def enqueue(self, intent: Intent) -> None:
        with self._lock:
            self._items.append(intent)

    def pop(self) -> Intent | None:
        with self._lock:
            return self._items.popleft() if self._items else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
