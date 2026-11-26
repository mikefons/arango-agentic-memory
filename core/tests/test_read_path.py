"""PF-1c/d: arms return no embeddings; the vector-index state is cached on request paths."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pytest
from arango.collection import StandardCollection
from arango.database import StandardDatabase
from arango.exceptions import IndexCreateError
from arango.request import Request
from arango.response import Response

from arango_memory.config import settings
from arango_memory.embedding import FakeEmbedder
from arango_memory.embedding_cache import embedding_cache
from arango_memory.ingest.store import store
from arango_memory.retrieve import search
from arango_memory.retrieve.search import RetrieveResult, retrieve
from arango_memory.schema import collections
from arango_memory.schema.collections import (
    drop_vector_index,
    ensure_vector_index,
    has_vector_index,
    vector_index_ready,
    vector_index_state,
)

_N_LISTS = 16
_CTX = {"tenant_id": "t_read", "agent_id": "a"}


class _CountingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__(dimensions=settings.embedding_dimensions)
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        return super().embed(text)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [FakeEmbedder.embed(self, t) for t in texts]


def _seed(db: StandardDatabase, wait: Callable[..., RetrieveResult], n: int) -> None:
    for i in range(n):
        store(db, content=f"memory {i} about Alice and subject {i}", turn_index=i, **_CTX)
    wait(db, query="Alice subject", **_CTX)


def _build_index(db: StandardDatabase) -> None:
    assert ensure_vector_index(db, dimensions=settings.embedding_dimensions, n_lists=_N_LISTS)


def _count_index_lookups(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real = collections.has_vector_index

    def counting(db: StandardDatabase, collection: str = "memories") -> bool:
        seen.append(collection)
        return real(db, collection)

    monkeypatch.setattr(collections, "has_vector_index", counting)
    return seen


def _reject_index_builds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    attempts: list[int] = []

    def rejected(self: StandardCollection, data: dict[str, Any], **_: Any) -> None:
        attempts.append(1)
        response = Response("post", "/_api/index", {}, 400, "Bad Request", "")
        raise IndexCreateError(response, Request("post", "/_api/index"))

    monkeypatch.setattr(StandardCollection, "add_index", rejected)
    return attempts


def test_arm_rows_carry_no_embedding(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(db, wait_for_searchable, 40)
    _build_index(db)
    rows: list[dict[str, Any]] = []
    real = search._run

    def recording(*args: Any) -> list[dict[str, Any]]:
        out = real(*args)
        rows.extend(out)
        return out

    monkeypatch.setattr(search, "_run", recording)
    result = retrieve(db, query="Alice subject 7", **_CTX)

    assert any("vector" in h.source for h in result.hits)
    assert any("graph" in h.source for h in result.hits)
    assert rows and not any("embedding" in row for row in rows)


def test_mmr_loads_embeddings_only_below_lambda_one(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(db, wait_for_searchable, 5)
    seen: list[list[int]] = []
    real = search._mmr

    def spying(candidates: list[Any], k: int) -> list[Any]:
        seen.append([len(c.embedding) for c in candidates])
        return real(candidates, k)

    monkeypatch.setattr(search, "_mmr", spying)
    retrieve(db, query="Alice subject", **_CTX)
    monkeypatch.setattr(settings, "mmr_lambda", 0.5)
    assert retrieve(db, query="Alice subject", **_CTX).hits

    assert set(seen[0]) == {0}
    assert set(seen[1]) == {settings.embedding_dimensions}


def test_query_is_embedded_only_when_something_reads_it(
    db: StandardDatabase, wait_for_searchable: Callable[..., RetrieveResult]
) -> None:
    _seed(db, wait_for_searchable, 40)
    emb = _CountingEmbedder()

    embedding_cache.clear()
    assert retrieve(db, query="Alice one", embedder=emb, n_lists=1000, **_CTX).hits
    assert emb.calls == 0  # no index, no fact keys → BM25 only, nothing to embed for

    assert retrieve(db, query="Alice two", embedder=emb, fact_keys=True, **_CTX).hits
    assert emb.calls == 1  # the fact-vector arm reads it

    _build_index(db)
    result = retrieve(db, query="Alice three", embedder=emb, **_CTX)
    assert emb.calls == 2
    assert any("vector" in h.source for h in result.hits)


def test_index_state_is_looked_up_once(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store(db, content="Alice keeps bees", **_CTX)
    collections.forget_vector_state(db)
    lookups = _count_index_lookups(monkeypatch)

    for _ in range(3):
        retrieve(db, query="bees", **_CTX)
    assert vector_index_state(db) == "deferred"

    assert lookups.count("memories") == 1


def test_rejected_build_waits_out_the_backoff(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(3):
        store(db, content=f"doc {i}", turn_index=i, **_CTX)
    attempts = _reject_index_builds(monkeypatch)
    dims = settings.embedding_dimensions
    monkeypatch.setattr(collections, "VECTOR_STATE_TTL_SECONDS", 0.0)

    for _ in range(3):
        assert vector_index_ready(db, dimensions=dims, n_lists=2) is False
    assert len(attempts) == 1  # the backoff outlives the ordinary state TTL

    monkeypatch.setattr(collections, "VECTOR_BUILD_BACKOFF_SECONDS", 0.0)
    collections.forget_vector_state(db)
    for _ in range(2):
        assert vector_index_ready(db, dimensions=dims, n_lists=2) is False
    assert len(attempts) == 3


def test_drop_clears_the_cached_state(
    db: StandardDatabase, wait_for_searchable: Callable[..., RetrieveResult]
) -> None:
    _seed(db, wait_for_searchable, 40)
    _build_index(db)
    assert vector_index_state(db) == "trained"

    assert drop_vector_index(db)

    assert vector_index_state(db) == "deferred"


def test_index_dropped_elsewhere_does_not_break_retrieval(
    db: StandardDatabase, wait_for_searchable: Callable[..., RetrieveResult]
) -> None:
    _seed(db, wait_for_searchable, 40)
    _build_index(db)
    assert vector_index_state(db) == "trained"  # cached
    memories = db.collection("memories")
    for index in memories.indexes():  # as another process would: our cache isn't told
        if index["type"] == "vector":
            memories.delete_index(index["id"])
    assert not has_vector_index(db)

    result = retrieve(db, query="Alice subject 7", **_CTX)

    assert result.hits
    assert not any("vector" in h.source for h in result.hits)
