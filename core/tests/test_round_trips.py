"""PF-1e: fewer round trips — access after the response, one embed per write, parallel arms."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from arango.database import StandardDatabase
from fastapi.testclient import TestClient

import arango_memory.api.app as app_mod
import arango_memory.retrieve.search as search
from arango_memory.client import ParallelArms
from arango_memory.embedding import FakeEmbedder
from arango_memory.embedding_cache import embedding_cache
from arango_memory.ingest.extract import ExtractedEntity, FakeExtractor
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import force_view_sync, retrieve
from arango_memory.telemetry import latency

_CTX = {"tenant_id": "rt", "agent_id": "a", "access_level": "write"}


def _access_counts(db: StandardDatabase) -> list[int]:
    return list(db.aql.execute(
        "FOR m IN memories FILTER m.tenant_id == 'rt' RETURN m.access_count"))  # type: ignore[arg-type]


def _seed(db: StandardDatabase) -> None:
    store(db, content="Alice met Bob in Paris", tenant_id="rt", agent_id="a")
    force_view_sync(db, "rt")  # searchable now, with no read to disturb access counts


# ── access write ──────────────────────────────────────────
def test_access_sink_takes_the_keys_instead_of_writing(db: StandardDatabase) -> None:
    _seed(db)
    seen: list[str] = []
    result = retrieve(db, query="Alice Paris", tenant_id="rt", agent_id="a",
                      access_sink=seen.extend)
    assert seen == [h.key for h in result.hits] and seen
    assert _access_counts(db) == [1]


@pytest.mark.parametrize("path,body", [
    ("/v1/retrieve", {"query": "Alice Paris", "ctx": _CTX}),
    ("/v1/prime", {"task": "Alice Paris", "ctx": _CTX}),
])
def test_api_writes_access_after_the_response_on_the_leased_handle(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, body: dict[str, Any]
) -> None:
    api.post("/v1/store", json={"content": "Alice met Bob in Paris", "ctx": _CTX, "sync": True})
    state = api.app.state  # type: ignore[attr-defined]
    order: list[str] = []
    real = app_mod.reset_access

    def spy(db: StandardDatabase, keys: list[str]) -> None:
        with state.db_pool._lock:
            assert db not in state.db_pool._idle  # still this request's handle
        order.append("access")
        real(db, keys)

    monkeypatch.setattr(app_mod, "reset_access", spy)
    monkeypatch.setattr(search, "reset_access", lambda *_: pytest.fail("written inline"))
    inner = api.app.middleware_stack  # type: ignore[attr-defined]

    async def tap(scope: Any, receive: Any, send: Any) -> None:
        async def tapped(message: Any) -> None:
            if message["type"] == "http.response.body" and not message.get("more_body"):
                order.append("sent")
            await send(message)

        await inner(scope, receive, tapped)

    api.app.middleware_stack = tap  # type: ignore[attr-defined]
    latency.clear()
    response = api.post(path, json=body)
    assert response.status_code == 200 and response.json()["hits"]
    assert order == ["sent", "access"]
    assert "retrieve.access" in latency.snapshot()
    assert _access_counts(api.app.state.client.db) == [2]  # type: ignore[attr-defined]


def test_failed_access_write_does_not_fail_the_request(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.post("/v1/store", json={"content": "Alice met Bob in Paris", "ctx": _CTX, "sync": True})

    def boom(*_: Any) -> None:
        raise RuntimeError("db gone")

    monkeypatch.setattr(app_mod, "reset_access", boom)
    response = api.post("/v1/retrieve", json={"query": "Alice Paris", "ctx": _CTX})
    assert response.status_code == 200 and response.json()["hits"]


# ── one embedding call per write ──────────────────────────
class _CountingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__(dimensions=64)
        self.calls: list[list[str]] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append([text])
        return super().embed(text)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [FakeEmbedder.embed(self, t) for t in texts]


def test_write_embeds_turn_and_names_together(db: StandardDatabase) -> None:
    embedding_cache.clear()
    emb = _CountingEmbedder()
    content = "Alice met Bob in Paris"
    result = store(db, content=content, tenant_id="rt", agent_id="a", embedder=emb)
    assert result.entity_ids
    assert len(emb.calls) == 1 and emb.calls[0][0] == content and len(emb.calls[0]) > 1


def test_failed_extraction_still_stores_the_memory(db: StandardDatabase) -> None:
    class _Broken(FakeExtractor):
        def extract(self, content: str) -> list[ExtractedEntity]:
            raise RuntimeError("extractor down")

    with pytest.raises(RuntimeError, match="extractor down"):
        store(db, content="Alice met Bob", tenant_id="rt", agent_id="a", extractor=_Broken())
    rows = list(db.aql.execute(  # type: ignore[arg-type]
        "FOR m IN memories FILTER m.tenant_id == 'rt' RETURN LENGTH(m.embedding)"))
    assert len(rows) == 1 and rows[0] > 0


# ── parallel arms ─────────────────────────────────────────
def test_arms_overlap_on_separate_handles_and_keep_order() -> None:
    all_inside = threading.Barrier(3, timeout=5)  # breaks unless the three overlap
    runner = ParallelArms(lambda: object(), 4)  # type: ignore[arg-type,return-value]
    mine: Any = object()

    def arm(name: str) -> Callable[[Any], tuple[str, Any]]:
        def run(handle: Any) -> tuple[str, Any]:
            all_inside.wait()
            return name, handle

        return run

    results = runner(mine, [arm("a"), arm("b"), arm("c")])
    runner.close()
    assert [n for n, _ in results] == ["a", "b", "c"]
    handles = [h for _, h in results]
    assert handles[0] is mine and len({id(h) for h in handles}) == 3


def test_arm_failure_propagates_after_the_others_finish() -> None:
    runner = ParallelArms(lambda: object(), 2)  # type: ignore[arg-type,return-value]
    finished = threading.Event()

    def boom(_: Any) -> None:
        raise RuntimeError("arm failed")

    def slow(_: Any) -> None:
        finished.wait(timeout=0.05)
        finished.set()

    with pytest.raises(RuntimeError, match="arm failed"):
        runner(object(), [boom, slow])  # type: ignore[arg-type]
    assert finished.is_set()
    runner.close()


@pytest.mark.parametrize("vector_on", [True, False])
def test_retrieve_uses_the_runner_only_with_the_vector_arm(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch, vector_on: bool
) -> None:
    _seed(db)
    monkeypatch.setattr(search, "vector_index_ready", lambda *_, **__: vector_on)
    monkeypatch.setattr(search, "_vector_arm", lambda *_: [])
    sizes: list[int] = []

    def runner(handle: StandardDatabase, tasks: Sequence[Callable[..., Any]]) -> list[Any]:
        sizes.append(len(tasks))
        return [task(handle) for task in tasks]

    result = retrieve(db, query="Alice Paris", tenant_id="rt", agent_id="a", arm_runner=runner)
    assert result.hits
    assert sizes == ([2] if vector_on else [])
