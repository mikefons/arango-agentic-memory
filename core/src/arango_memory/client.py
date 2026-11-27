"""ArangoDB client + connection abstraction.

Works against both self-hosted/Docker and ArangoGraph cloud via the same
connection settings (DESIGN.md §6). Authentication falls back from bearer
token (ArangoGraph) to basic auth (local).
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from typing import Any

from arango import ArangoClient  # type: ignore[attr-defined]
from arango.database import StandardDatabase

from .config import Settings, settings
from .transport import ObservedHTTPClient


class DatabasePool:
    """Up to `size` database handles, each leased to one thread at a time.

    python-arango's HTTP session isn't guaranteed thread-safe, so concurrent callers each
    need their own handle. Handles are built on first use and reused (newest first), which
    keeps their connections warm however long the calling threads live.
    """

    def __init__(self, factory: Callable[[], StandardDatabase], size: int) -> None:
        self._factory = factory
        self._free = threading.BoundedSemaphore(size)
        self._idle: list[StandardDatabase] = []
        self._lock = threading.Lock()

    @contextmanager
    def lease(self) -> Iterator[StandardDatabase]:
        """A handle for the duration of the block; waits when all `size` are out."""
        with self._free:
            with self._lock:
                db = self._idle.pop() if self._idle else None
            if db is None:
                db = self._factory()
            try:
                yield db
            finally:
                with self._lock:
                    self._idle.append(db)


ArmTask = Callable[[StandardDatabase], Any]
# Runs independent queries and returns their results in order; the first may use `db`.
ArmRunner = Callable[[StandardDatabase, Sequence[ArmTask]], list[Any]]


class ParallelArms:
    """An `ArmRunner` that runs every task after the first on its own handle and thread.

    The helper pool is separate from the callers' pool, and a task holds one handle and
    waits for nothing else, so callers that all want helpers queue instead of deadlocking.
    """

    def __init__(self, factory: Callable[[], StandardDatabase], size: int) -> None:
        self._pool = DatabasePool(factory, size)
        self._threads = ThreadPoolExecutor(max_workers=size, thread_name_prefix="arm")

    def _on_own_handle(self, task: ArmTask) -> Any:
        with self._pool.lease() as db:
            return task(db)

    def __call__(self, db: StandardDatabase, tasks: Sequence[ArmTask]) -> list[Any]:
        first, *rest = tasks
        futures = [
            # A copy of the caller's context keeps request ids and trace spans in the thread.
            self._threads.submit(contextvars.copy_context().run, self._on_own_handle, task)
            for task in rest
        ]
        try:
            head = first(db)
        finally:
            wait(futures)  # never leave a query running past its request
        return [head, *(future.result() for future in futures)]

    def close(self) -> None:
        self._threads.shutdown()


PROBE_TIMEOUT_SECONDS = 3.0  # per database call made by a health probe


class ArangoMemoryClient:
    """Thin wrapper around python-arango exposing the memory database handle."""

    def __init__(self, config: Settings | None = None) -> None:
        self._config = config or settings
        # verify_override controls TLS verification for HTTPS (ArangoGraph).
        # Harmless for plain-http local connections.
        self._client = ArangoClient(
            hosts=self._config.arango_url,
            verify_override=self._config.arango_tls_verify,
            http_client=ObservedHTTPClient(
                request_timeout=self._config.arango_request_timeout,
                connect_timeout=self._config.arango_connect_timeout,
                slow_seconds=self._config.arango_slow_call_seconds,
                max_idle_seconds=self._config.arango_max_idle_seconds,
            ),
        )
        self._db: StandardDatabase | None = None

    def probe(self) -> ArangoMemoryClient:
        """A client for health checks, on its own connection, with short timeouts.

        A call that gets no answer is not repeated: the default transport retries a read that
        timed out, and tries the host again after that, so against a database that stops
        answering one call lasts many timeouts. A call whose connection breaks (an idle one
        reset by the far end, say) is tried once more, so that alone doesn't report the
        database down."""
        probe = ArangoMemoryClient(self._config)
        probe._client = ArangoClient(
            hosts=self._config.arango_url,
            verify_override=self._config.arango_tls_verify,
            http_client=ObservedHTTPClient(
                request_timeout=PROBE_TIMEOUT_SECONDS,
                connect_timeout=PROBE_TIMEOUT_SECONDS,
                slow_seconds=self._config.arango_slow_call_seconds,
                max_idle_seconds=self._config.arango_max_idle_seconds,
                retry_attempts=0,
                resend_dropped=True,
            ),
            resolver_max_tries=1,
        )
        probe._db = probe.database(self._config.arango_db)
        return probe

    @property
    def config(self) -> Settings:
        return self._config

    @property
    def db(self) -> StandardDatabase:
        if self._db is None:
            raise RuntimeError("Client not connected; call connect() first.")
        return self._db

    def connect(self) -> StandardDatabase:
        """Connect to (and lazily create) the memory database."""
        cfg = self._config
        sys_db = self._sys_db()
        if not sys_db.has_database(cfg.arango_db):
            sys_db.create_database(cfg.arango_db)
        self._db = self.database(cfg.arango_db)
        return self._db

    def database(self, name: str) -> StandardDatabase:
        """A handle on any database on the server, with the configured credentials."""
        cfg = self._config
        if cfg.arango_bearer_token:
            return self._client.db(name, user_token=cfg.arango_bearer_token)
        return self._client.db(name, username=cfg.arango_username, password=cfg.arango_password)

    def pool(self, size: int) -> DatabasePool:
        """A pool of handles on the memory database, each with its own connection."""
        return DatabasePool(self._new_handle, size)

    def parallel_arms(self, size: int) -> ParallelArms:
        """A runner for concurrent queries, with up to `size` connections of its own."""
        return ParallelArms(self._new_handle, size)

    def _new_handle(self) -> StandardDatabase:
        cfg = self._config
        return ArangoMemoryClient(cfg).database(cfg.arango_db)

    def _sys_db(self) -> StandardDatabase:
        return self.database("_system")

    def ping(self) -> bool:
        """Health check used by the API /health endpoint."""
        try:
            self.db.version()
            return True
        except Exception:
            return False

    def describe(self) -> dict[str, str]:
        """Connection metadata for diagnostics (no secrets)."""
        return {
            "target": self._config.arango_target,
            "url": self._config.arango_url,
            "database": self._config.arango_db,
            "auth": "bearer_token" if self._config.arango_bearer_token else "basic",
            "tls_verify": str(self._config.arango_tls_verify),
        }
