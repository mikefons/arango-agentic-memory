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
from ..telemetry.logging import logger, request_id_var, tenant_var
from .extract import Extractor, get_extractor
from .procedural import record_step
from .queue import Claim, Intent, StepIntent, WriteIntent, WriteQueue
from .store import StoreItem, store, store_many

_POLL_INTERVAL = 0.05


def commit_intent(
    db: StandardDatabase,
    intent: Intent,
    *,
    embedder: Embedder,
    extractor: Extractor,
    generator: Generator,
) -> None:
    """Commit one write/step intent to ArangoDB.

    The single commit path shared by the async worker (which wraps it in
    retry/dead-letter) and the synchronous store path (MA-1, inline on the request
    thread). Commits are idempotency-keyed, so a sync commit and a duplicate async
    commit converge on the same records safely.
    """
    if isinstance(intent, StepIntent):
        record_step(
            db,
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
            db,
            content=intent.content,
            tenant_id=intent.tenant_id,
            agent_id=intent.agent_id,
            session_id=intent.session_id,
            turn_index=intent.turn_index,
            embedder=embedder,
            extractor=extractor,
            generator=generator,
            mode=settings.memory_mode,
            source_reliability=intent.source_reliability,
            memory_type=intent.memory_type,
        )


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
        batch_size: int | None = None,
    ) -> None:
        self._queue = queue
        self._db = db
        self._embedder = embedder or get_embedder()
        self._extractor = extractor or get_extractor()
        self._generator = generator or get_generator()
        self._max_retries = max_retries if max_retries is not None else settings.write_max_retries
        self._backoff = backoff_base if backoff_base is not None else settings.write_backoff_base
        self._batch_size = batch_size if batch_size is not None else settings.write_batch_size
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Processing ────────────────────────────────────────
    def process(self, intent: Intent) -> bool:
        """Commit one intent with retry/backoff. Returns True if committed."""
        # Correlation for any log lines from this commit (worker runs off-request);
        # reset after so `drain()` (called on the request/test thread) doesn't leak.
        rid_token = request_id_var.set(intent.key)
        tenant_token = tenant_var.set(intent.tenant_id)
        last_error: Exception | None = None
        try:
            for attempt in range(self._max_retries):
                try:
                    self._commit(intent)
                    return True
                except Exception as exc:  # noqa: BLE001 — durability: isolate write failures
                    last_error = exc
                    if attempt < self._max_retries - 1 and self._backoff > 0:
                        time.sleep(self._backoff * (2**attempt))
            self._dead_letter(intent, last_error)
            return False
        finally:
            request_id_var.reset(rid_token)
            tenant_var.reset(tenant_token)

    def _commit(self, intent: Intent) -> None:
        commit_intent(
            self._db,
            intent,
            embedder=self._embedder,
            extractor=self._extractor,
            generator=self._generator,
        )

    # ── Batch commit (WQ-1b) ──────────────────────────────
    def _claim_batch(self) -> list[Claim]:
        """Lease the next round of work: one intent, or up to `batch_size` in one round trip."""
        if self._batch_size <= 1:
            claim = self._queue.claim()
            return [claim] if claim is not None else []
        return self._queue.claim_many(self._batch_size)

    @staticmethod
    def _batchable(intent: Intent) -> bool:
        """Intents `store_many` commits exactly like `store()`. It skips full-mode prospective
        indexing, working-capacity eviction and per-session topic tracking, so anything that
        needs those (a session, working memory, full mode) stays on the per-intent path."""
        return (
            isinstance(intent, WriteIntent)
            and intent.session_id is None
            and intent.memory_type == "episodic"
            and settings.memory_mode == "lite"
        )

    def _store_batch(self, group: list[Claim]) -> bool:
        """Commit one tenant+agent group through a single `store_many`. False = it failed."""
        first = group[0].intent
        assert isinstance(first, WriteIntent)
        items = [
            StoreItem(
                content=c.intent.content,
                turn_index=c.intent.turn_index,
                source_reliability=c.intent.source_reliability,
            )
            for c in group
            if isinstance(c.intent, WriteIntent)
        ]
        tenant_token = tenant_var.set(first.tenant_id)
        try:
            store_many(
                self._db, items, tenant_id=first.tenant_id, agent_id=first.agent_id,
                embedder=self._embedder, extractor=self._extractor,
                mode=settings.memory_mode, extract=True,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — a bad batch falls back to per-intent retry
            logger.warning(
                "batch commit failed; retrying per intent",
                extra={"tenant_id": first.tenant_id, "size": len(group), "error": str(exc)},
            )
            return False
        finally:
            tenant_var.reset(tenant_token)

    def _commit_claims(self, claims: list[Claim]) -> None:
        """Commit a round of claims, then ack them all. Batchable intents are grouped by
        (tenant, agent) and bulk-committed; a failed group, and everything not batchable, goes
        through `process()` one intent at a time, so retry and dead-lettering still isolate a bad
        intent. Commits are idempotency-keyed, so a retry after a partial batch is safe."""
        singles: list[Claim] = []
        groups: dict[tuple[str, str], list[Claim]] = {}
        for claim in claims:
            intent = claim.intent
            if len(claims) > 1 and self._batchable(intent):
                groups.setdefault((intent.tenant_id, intent.agent_id), []).append(claim)
            else:
                singles.append(claim)
        for group in groups.values():
            if len(group) == 1 or not self._store_batch(group):
                singles.extend(group)
        for claim in singles:
            self.process(claim.intent)  # commits or dead-letters; either way it's done
        self._queue.ack_many(claims)

    def drain(self) -> int:
        """Process all currently queued intents synchronously. Returns count handled."""
        handled = 0
        while claims := self._claim_batch():
            self._commit_claims(claims)
            handled += len(claims)
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
        logger.error(
            "write dead-lettered",
            extra={
                "kind": "step" if isinstance(intent, StepIntent) else "write",
                "key": intent.key,
                "error": str(error),
            },
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
            claims = self._claim_batch()
            if not claims:
                self._stop.wait(_POLL_INTERVAL)
                continue
            self._commit_claims(claims)  # finishes the in-flight round before re-checking stop
