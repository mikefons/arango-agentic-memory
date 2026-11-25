"""PF-1a: requests run in parallel — handlers off the event loop, one DB handle each."""

from __future__ import annotations

import inspect
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from arango.database import StandardDatabase
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import arango_memory.api.app as app_mod
from arango_memory.client import ArangoMemoryClient, DatabasePool
from arango_memory.config import Settings
from arango_memory.embedding import FakeEmbedder
from arango_memory.embedding_cache import EmbeddingCache
from arango_memory.ingest.extract import (
    FakeExtractor,
    SerializedExtractor,
    serialize_if_cpu_bound,
)
from arango_memory.lifecycle.decay import reset_access
from arango_memory.retrieve.search import RetrieveResult
from arango_memory.telemetry.logging import logger, tenant_var

# These push their own blocking calls to a thread (or only wait).
_ASYNC_ROUTES = {"/health", "/ready", "/v1/flush", "/v1/store", "/v1/step"}
_BODY = {"query": "x", "ctx": {"tenant_id": "t", "agent_id": "a"}}


def test_blocking_routes_are_not_coroutines(api: TestClient) -> None:
    routes = [r for r in api.app.routes if isinstance(r, APIRoute)]  # type: ignore[attr-defined]
    coroutines = {r.path for r in routes if inspect.iscoroutinefunction(r.endpoint)}
    assert coroutines == _ASYNC_ROUTES


def test_retrieves_run_in_parallel_and_health_answers(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    both_inside = threading.Barrier(2, timeout=5)  # breaks unless the two overlap
    release = threading.Event()
    handles: list[StandardDatabase] = []

    def slow_retrieve(db: StandardDatabase, **_: Any) -> RetrieveResult:
        handles.append(db)
        both_inside.wait()
        release.wait(timeout=5)
        return RetrieveResult()

    monkeypatch.setattr(app_mod, "retrieve", slow_retrieve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(api.post, "/v1/retrieve", json=_BODY) for _ in range(2)]
        try:
            deadline = time.monotonic() + 5
            while len(handles) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert api.get("/health").status_code == 200  # while both are still running
            assert not any(c.done() for c in calls)
        finally:
            release.set()
        assert [c.result().status_code for c in calls] == [200, 200]
    assert handles[0] is not handles[1]  # never one handle on two threads


def test_pool_reuses_handles_and_caps_them() -> None:
    built: list[object] = []

    def factory() -> Any:
        built.append(object())
        return built[-1]

    pool = DatabasePool(factory, 2)
    with pool.lease() as first, pool.lease() as second:
        assert first is not second
        waiter_got = threading.Event()

        def third() -> None:
            with pool.lease():
                waiter_got.set()

        thread = threading.Thread(target=third)
        thread.start()
        assert not waiter_got.wait(timeout=0.1)  # all handles out → it waits
    thread.join(timeout=5)
    assert waiter_got.is_set()
    assert len(built) == 2


def test_pool_returns_the_handle_when_the_block_raises() -> None:
    pool = DatabasePool(lambda: object(), 1)  # type: ignore[arg-type, return-value]
    with pytest.raises(ValueError), pool.lease():
        raise ValueError
    with pool.lease() as db:
        assert db is not None


def test_embedding_cache_survives_concurrent_eviction() -> None:
    cache = EmbeddingCache(max_size=8)  # tiny, so every thread evicts constantly
    embedder = FakeEmbedder(dimensions=8)

    def hammer(worker: int) -> None:
        for i in range(300):
            texts = [f"w{worker}-{i % 20}", f"shared-{i % 12}"]
            cache.embed_many(embedder, texts, tenant_id="t")
            cache.embed(embedder, texts[1], tenant_id="t")

    with ThreadPoolExecutor(max_workers=8) as pool:
        for fut in [pool.submit(hammer, w) for w in range(8)]:
            fut.result()
    assert len(cache._store) <= 8


def test_only_cpu_bound_extractors_are_serialised() -> None:
    class CpuBound(FakeExtractor):
        io_bound = False

    class IoBound(FakeExtractor):
        io_bound = True

    wrapped = serialize_if_cpu_bound(CpuBound())
    assert isinstance(wrapped, SerializedExtractor)
    assert serialize_if_cpu_bound(wrapped) is wrapped  # no double wrap
    io = IoBound()
    assert serialize_if_cpu_bound(io) is io


def test_concurrent_access_bumps_do_not_conflict(
    db: StandardDatabase, settings: Settings
) -> None:
    keys = [f"m{i}" for i in range(50)]
    for key in keys:
        db.collection("memories").insert({"_key": key, "idempotency_key": key})
    pool = ArangoMemoryClient(settings).pool(8)

    def bump() -> None:
        for _ in range(10):
            with pool.lease() as handle:
                reset_access(handle, keys)

    with ThreadPoolExecutor(max_workers=8) as workers:
        for fut in [workers.submit(bump) for _ in range(8)]:
            fut.result()  # a write-write conflict would raise here


def test_access_log_keeps_the_tenant(api: TestClient) -> None:
    """The handler runs in a worker thread; the access line must still name the tenant."""
    seen: list[str | None] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.getMessage() == "request":
                seen.append(tenant_var.get())

    handler = Capture()
    logger.addHandler(handler)
    try:
        assert api.post("/v1/retrieve", json=_BODY).status_code == 200
    finally:
        logger.removeHandler(handler)
    assert seen == ["t"]
