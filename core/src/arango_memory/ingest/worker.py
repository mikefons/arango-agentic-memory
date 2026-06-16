"""Durable write worker (DESIGN.md §15).

Drains the write queue and commits each intent via `store()` with exponential
backoff. Intents that exhaust their retries are dead-lettered to the
`failed_writes` collection (idempotency-keyed, so repeats don't pile up) for
inspection and replay. Because commits are idempotency-keyed, retries and
replays cannot duplicate records.

`drain()` is synchronous (used by tests for determinism); `start()/stop()` run
the same loop on a daemon thread for the live service. The worker uses its own
DB connection — request threads only enqueue.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from typing import cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..embedding import Embedder, get_embedder
from ..generation import Generator, get_generator
from ..models import utcnow_iso
from ..schema.collections import DEAD_LETTER_COLLECTION
from ..telemetry import metrics
from .extract import Extractor, get_extractor
from .procedural import record_step
from .queue import Intent, StepIntent, WriteIntent, WriteQueue
from .store import store

_POLL_INTERVAL = 0.05


class WriteWorker:
    def __init__(
        self,
        queue: WriteQueue,
        db: StandardDatabase,
        *,
        embedder: Embedder | None = None,
        extractor: Extractor | None = None,
        generator: Generator | None = None,
        max_retries: int | None = None,
        backoff_base: float | None = None,
    ) -> None:
        self._queue = queue
        self._db = db
        self._embedder = embedder or get_embedder()
        self._extractor = extractor or get_extractor()
        self._generator = generator or get_generator()
        self._max_retries = max_retries if max_retries is not None else settings.write_max_retries
        self._backoff = backoff_base if backoff_base is not None else settings.write_backoff_base
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Processing ────────────────────────────────────────
    def process(self, intent: Intent) -> bool:
        """Commit one intent with retry/backoff. Returns True if committed."""
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                self._commit(intent)
                return True
            except Exception as exc:  # noqa: BLE001 — durability: isolate all write failures
                last_error = exc
                if attempt < self._max_retries - 1 and self._backoff > 0:
                    time.sleep(self._backoff * (2**attempt))
        self._dead_letter(intent, last_error)
        return False

    def _commit(self, intent: Intent) -> None:
        if isinstance(intent, StepIntent):
            record_step(
                self._db,
                tool_name=intent.tool_name,
                arguments=intent.arguments,
                outcome=intent.outcome,
                tenant_id=intent.tenant_id,
                agent_id=intent.agent_id,
                pattern_summary=intent.pattern_summary,
                source_memory_key=intent.source_memory_key,
                prev_step_key=intent.prev_step_key,
            )
        else:
            store(
                self._db,
                content=intent.content,
                tenant_id=intent.tenant_id,
                agent_id=intent.agent_id,
                session_id=intent.session_id,
                turn_index=intent.turn_index,
                embedder=self._embedder,
                extractor=self._extractor,
                generator=self._generator,
                mode=settings.memory_mode,
                source_reliability=intent.source_reliability,
                memory_type=intent.memory_type,
            )

    def drain(self) -> int:
        """Process all currently queued intents synchronously. Returns count handled."""
        handled = 0
        while (claim := self._queue.claim()) is not None:
            self.process(claim.intent)  # commits or dead-letters; either way it's done
            self._queue.ack(claim)
            handled += 1
        return handled

    def _dead_letter(self, intent: Intent, error: Exception | None) -> None:
        self._db.collection(DEAD_LETTER_COLLECTION).insert(
            {
                "_key": intent.key,
                "kind": "step" if isinstance(intent, StepIntent) else "write",
                "intent": dataclasses.asdict(intent),
                "error": str(error),
                "attempts": self._max_retries,
                "failed_at": utcnow_iso(),
            },
            overwrite_mode="replace",
            silent=True,
        )
        metrics.emit("write", dead_lettered=True)

    def replay_failed(self) -> int:
        """Re-enqueue dead-lettered intents and clear them. Returns count replayed."""
        collection = self._db.collection(DEAD_LETTER_COLLECTION)
        replayed = 0
        for doc in cast(Cursor, collection.all()):
            data = doc["intent"]
            intent: Intent = (
                StepIntent(**data) if doc.get("kind") == "step" else WriteIntent(**data)
            )
            self._queue.enqueue(intent)
            collection.delete(doc["_key"])
            replayed += 1
        return replayed

    # ── Background thread ─────────────────────────────────
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="write-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            claim = self._queue.claim()
            if claim is None:
                self._stop.wait(_POLL_INTERVAL)
                continue
            self.process(claim.intent)  # finishes the in-flight intent before re-checking stop
            self._queue.ack(claim)
