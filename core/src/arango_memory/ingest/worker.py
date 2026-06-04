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

import threading
import time
from typing import cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..embedding import Embedder, get_embedder
from ..models import utcnow_iso
from ..schema.collections import DEAD_LETTER_COLLECTION
from .extract import Extractor, get_extractor
from .queue import WriteIntent, WriteQueue
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
        max_retries: int | None = None,
        backoff_base: float | None = None,
    ) -> None:
        self._queue = queue
        self._db = db
        self._embedder = embedder or get_embedder()
        self._extractor = extractor or get_extractor()
        self._max_retries = max_retries if max_retries is not None else settings.write_max_retries
        self._backoff = backoff_base if backoff_base is not None else settings.write_backoff_base
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Processing ────────────────────────────────────────
    def process(self, intent: WriteIntent) -> bool:
        """Commit one intent with retry/backoff. Returns True if committed."""
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                store(
                    self._db,
                    content=intent.content,
                    tenant_id=intent.tenant_id,
                    agent_id=intent.agent_id,
                    session_id=intent.session_id,
                    turn_index=intent.turn_index,
                    embedder=self._embedder,
                    extractor=self._extractor,
                )
                return True
            except Exception as exc:  # noqa: BLE001 — durability: isolate all write failures
                last_error = exc
                if attempt < self._max_retries - 1 and self._backoff > 0:
                    time.sleep(self._backoff * (2**attempt))
        self._dead_letter(intent, last_error)
        return False

    def drain(self) -> int:
        """Process all currently queued intents synchronously. Returns count handled."""
        handled = 0
        while (intent := self._queue.pop()) is not None:
            self.process(intent)
            handled += 1
        return handled

    def _dead_letter(self, intent: WriteIntent, error: Exception | None) -> None:
        self._db.collection(DEAD_LETTER_COLLECTION).insert(
            {
                "_key": intent.key,
                "intent": {
                    "content": intent.content,
                    "tenant_id": intent.tenant_id,
                    "agent_id": intent.agent_id,
                    "session_id": intent.session_id,
                    "turn_index": intent.turn_index,
                },
                "error": str(error),
                "attempts": self._max_retries,
                "failed_at": utcnow_iso(),
            },
            overwrite_mode="replace",
            silent=True,
        )

    def replay_failed(self) -> int:
        """Re-enqueue dead-lettered intents and clear them. Returns count replayed."""
        collection = self._db.collection(DEAD_LETTER_COLLECTION)
        replayed = 0
        for doc in cast(Cursor, collection.all()):
            data = doc["intent"]
            self._queue.enqueue(WriteIntent(**data))
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
            intent = self._queue.pop()
            if intent is None:
                self._stop.wait(_POLL_INTERVAL)
                continue
            self.process(intent)
