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
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..embedding import Embedder, get_embedder
from ..generation import Generator, get_generator
from ..models import utcnow_iso
from ..schema.collections import DEAD_LETTER_COLLECTION
from ..telemetry import metrics
from ..telemetry.logging import logger, request_id_var, tenant_var
from .extract import ExtractedEntity, ExtractedRelation, Extractor, get_extractor, is_io_bound
from .procedural import record_step
from .queue import Claim, Intent, StepIntent, WriteIntent, WriteQueue
from .store import StoreItem, store, store_many

_POLL_INTERVAL = 0.05
_ERROR_BACKOFF_MAX = 30.0  # longest pause after repeated worker errors (seconds)
# ArangoDB errors that mean "another writer got there first": write-write conflict and unique
# constraint violation. Concurrent agent groups can hit them on a shared entity or edge.
_CONFLICT_CODES = frozenset({1200, 1210})
_CONFLICT_RETRIES = 3


def _is_conflict(exc: BaseException) -> bool:
    return getattr(exc, "error_code", None) in _CONFLICT_CODES


class _SerializedExtractor:
    """Lets agent groups share a CPU-bound extractor: calls take a lock, so the models run one
    at a time (they contend under the GIL anyway) while the DB round trips overlap."""

    io_bound = False

    def __init__(self, inner: Extractor) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self.name = inner.name

    def extract(self, text: str) -> list[ExtractedEntity]:
        with self._lock:
            return self._inner.extract(text)

    def extract_relations(
        self, text: str, entities: Sequence[ExtractedEntity]
    ) -> list[ExtractedRelation]:
        with self._lock:
            return self._inner.extract_relations(text, entities)

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._inner, attr)


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
        concurrency: int | None = None,
        db_factory: Callable[[], StandardDatabase] | None = None,
    ) -> None:
        self._queue = queue
        self._db = db
        self._embedder = embedder or get_embedder()
        self._extractor = extractor or get_extractor()
        self._generator = generator or get_generator()
        self._max_retries = max_retries if max_retries is not None else settings.write_max_retries
        self._backoff = backoff_base if backoff_base is not None else settings.write_backoff_base
        self._batch_size = batch_size if batch_size is not None else settings.write_batch_size
        self._concurrency = (
            concurrency if concurrency is not None else settings.write_worker_concurrency
        )
        if self._concurrency > 1 and not is_io_bound(self._extractor):
            self._extractor = _SerializedExtractor(self._extractor)
        # Each pool thread opens its own connection (a shared one is not safe to drive from
        # several threads); without a factory the pool shares `db`.
        self._db_factory = db_factory
        self._local = threading.local()
        self._pool: ThreadPoolExecutor | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _conn(self) -> StandardDatabase:
        """The DB handle for the calling thread: the worker's own, or a pool thread's."""
        if self._db_factory is None or not getattr(self._local, "in_pool", False):
            return self._db
        db = getattr(self._local, "db", None)
        if db is None:
            db = self._local.db = self._db_factory()
        return cast(StandardDatabase, db)

    def _mark_pool_thread(self) -> None:
        self._local.in_pool = True

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
            self._conn(),
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
            for attempt in range(_CONFLICT_RETRIES + 1):
                try:
                    store_many(
                        self._conn(), items, tenant_id=first.tenant_id, agent_id=first.agent_id,
                        embedder=self._embedder, extractor=self._extractor,
                        mode=settings.memory_mode, extract=True,
                    )
                    return True
                except Exception as exc:  # noqa: BLE001
                    # Another agent group wrote the same entity or edge at the same moment.
                    # Commits are idempotency-keyed, so run the batch again before giving up.
                    if _is_conflict(exc) and attempt < _CONFLICT_RETRIES:
                        logger.info(
                            "batch hit a write conflict; retrying",
                            extra={"tenant_id": first.tenant_id, "attempt": attempt + 1},
                        )
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    raise
            return False
        except Exception as exc:  # noqa: BLE001 — a bad batch falls back to per-intent retry
            logger.warning(
                "batch commit failed; retrying per intent",
                extra={"tenant_id": first.tenant_id, "size": len(group), "error": str(exc)},
            )
            return False
        finally:
            tenant_var.reset(tenant_token)

    def _commit_unit(self, claims: list[Claim], *, batching: bool) -> None:
        """Commit one tenant+agent's claims in order: batchable intents through a single
        `store_many`, and a failed batch plus everything not batchable through `process()` one
        intent at a time, so retry and dead-lettering still isolate a bad intent."""
        group = [c for c in claims if batching and self._batchable(c.intent)]
        singles = [c for c in claims if c not in group]
        if len(group) == 1 or (group and not self._store_batch(group)):
            singles = group + singles
        for claim in singles:
            self.process(claim.intent)  # commits or dead-letters; either way it's done

    def _run_unit(self, claims: list[Claim], batching: bool) -> None:
        self._mark_pool_thread()
        self._commit_unit(claims, batching=batching)

    def _commit_claims(self, claims: list[Claim]) -> None:
        """Commit a round of claims, then ack them all. The claims split into one unit per
        (tenant, agent); with `write_worker_concurrency` > 1 the units run in parallel, each
        agent's writes still in order on a single thread. Commits are idempotency-keyed, so a
        retry after a partial batch is safe."""
        units: dict[tuple[str, str], list[Claim]] = {}
        for claim in claims:
            units.setdefault((claim.intent.tenant_id, claim.intent.agent_id), []).append(claim)
        batching = len(claims) > 1
        if self._concurrency > 1 and len(units) > 1:
            pool = self._ensure_pool()
            futures: list[Future[None]] = [
                pool.submit(self._run_unit, unit, batching) for unit in units.values()
            ]
            errors = [f.exception() for f in futures]  # waits for every unit before releasing
            failed = next((e for e in errors if e is not None), None)
            if failed is not None:
                raise failed
        else:
            for unit in units.values():
                self._commit_unit(unit, batching=batching)
        self._queue.ack_many(claims)

    def _ensure_pool(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=self._concurrency, thread_name_prefix="write-unit"
            )
        return self._pool

    def drain(self) -> int:
        """Process all currently queued intents synchronously. Returns count handled."""
        handled = 0
        while claims := self._claim_batch():
            self._commit_claims(claims)
            handled += len(claims)
        return handled

    def _dead_letter(self, intent: Intent, error: Exception | None) -> None:
        self._conn().collection(DEAD_LETTER_COLLECTION).insert(
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
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def is_alive(self) -> bool:
        """True while the background thread is running. `/health` reports it, because a dead
        worker leaves every queued write undrained and nothing else shows it."""
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            claims: list[Claim] = []
            try:
                claims = self._claim_batch()
                if not claims:
                    failures = 0
                    self._stop.wait(_POLL_INTERVAL)
                    continue
                self._commit_claims(claims)  # finishes the in-flight round before re-checking stop
                failures = 0
            except Exception as exc:  # noqa: BLE001 — the worker thread must never die
                # A transient error (a DB blip while claiming or acking, a failure while
                # dead-lettering) used to kill this thread for good, leaving every queued write
                # undrained until the process restarted. Release what we hold, back off, go on.
                failures += 1
                logger.error(
                    "write worker iteration failed; will retry",
                    extra={"error": str(exc), "consecutive_failures": failures},
                    exc_info=True,
                )
                self._release(claims)
                self._stop.wait(min(_ERROR_BACKOFF_MAX, 0.5 * 2 ** min(failures, 6)))

    def _release(self, claims: list[Claim]) -> None:
        """Best-effort: hand leased intents back so they are retried (commits are
        idempotency-keyed, so a re-commit is safe). A durable queue would redeliver them when the
        lease expires anyway; the in-process queue never would."""
        for claim in claims:
            try:
                self._queue.nack(claim)
            except Exception:  # noqa: BLE001 — releasing must not raise out of the error path
                logger.warning("could not release a leased write", extra={"handle": claim.handle})
