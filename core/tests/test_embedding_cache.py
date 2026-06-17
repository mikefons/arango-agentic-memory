"""Dedicated embedding cache — per-tenant memoization (DESIGN.md §16/§24)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest
from arango.database import StandardDatabase

from arango_memory.config import settings
from arango_memory.embedding import FakeEmbedder
from arango_memory.embedding_cache import (
    EmbeddingCache,
    embed_batch_cached,
    embed_cached,
    embedding_cache,
)
from arango_memory.ingest.store import store
from arango_memory.telemetry import metrics


class _CountingEmbedder(FakeEmbedder):
    def __init__(self, dimensions: int = 256) -> None:
        super().__init__(dimensions=dimensions)
        self.calls = 0
        self.batch_calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        return super().embed(text)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        self.batch_calls += 1
        base = FakeEmbedder.embed  # avoid bumping `calls` via the overridden embed
        return [base(self, t) for t in texts]


# ── unit (no DB) ──────────────────────────────────────────
def test_repeat_is_a_hit() -> None:
    cache, emb = EmbeddingCache(), _CountingEmbedder()
    v1 = cache.embed(emb, "alpha", tenant_id="t")
    v2 = cache.embed(emb, "alpha", tenant_id="t")
    assert v1 == v2
    assert emb.calls == 1            # provider hit only once
    assert cache.hit_rate == 0.5     # 1 hit / 2 lookups


def test_per_tenant_namespacing() -> None:
    cache, emb = EmbeddingCache(), _CountingEmbedder()
    cache.embed(emb, "secret", tenant_id="t1")
    cache.embed(emb, "secret", tenant_id="t2")  # different tenant → miss
    assert emb.calls == 2  # no cross-tenant hit (§24 timing-attack defense)


def test_dimensions_in_key_prevents_collision() -> None:
    cache = EmbeddingCache()
    big = cache.embed(_CountingEmbedder(dimensions=256), "x", tenant_id="t")
    small = cache.embed(_CountingEmbedder(dimensions=8), "x", tenant_id="t")
    assert len(big) == 256 and len(small) == 8  # same text/model, no wrong-length hit


def test_lru_eviction() -> None:
    cache, emb = EmbeddingCache(max_size=2), _CountingEmbedder()
    cache.embed(emb, "a", tenant_id="t")
    cache.embed(emb, "b", tenant_id="t")
    cache.embed(emb, "c", tenant_id="t")  # evicts "a"
    cache.embed(emb, "a", tenant_id="t")  # miss again
    assert emb.calls == 4


def test_emits_metric() -> None:
    events: list[dict[str, object]] = []
    metrics.on("embedding_cache", lambda **p: events.append(p))
    cache, emb = EmbeddingCache(), _CountingEmbedder()
    cache.embed(emb, "a", tenant_id="t")
    cache.embed(emb, "a", tenant_id="t")
    metrics.clear()
    assert [e["hit"] for e in events] == [False, True]


def test_embed_many_one_batch_call_for_misses() -> None:
    cache, emb = EmbeddingCache(), _CountingEmbedder()
    vecs = cache.embed_many(emb, ["a", "b", "a", "c"], tenant_id="t")
    assert set(vecs) == {"a", "b", "c"}      # deduped
    assert emb.batch_calls == 1              # single provider round-trip
    assert emb.calls == 0                    # never used the one-at-a-time path
    # A second pass is all hits → no new batch call.
    cache.embed_many(emb, ["a", "b", "c"], tenant_id="t")
    assert emb.batch_calls == 1


def test_embed_many_batches_only_the_misses() -> None:
    cache, emb = EmbeddingCache(), _CountingEmbedder()
    cache.embed(emb, "a", tenant_id="t")     # warm "a"
    captured: list[int] = []
    original = emb.embed_batch

    def spy(texts: Sequence[str]) -> list[list[float]]:
        captured.append(len(texts))
        return original(texts)

    emb.embed_batch = spy  # type: ignore[method-assign]
    cache.embed_many(emb, ["a", "b", "c"], tenant_id="t")
    assert captured == [2]  # only "b","c" were embedded; "a" served from cache


@pytest.fixture
def _restore_flag() -> Iterator[None]:
    original = settings.embedding_cache
    yield
    settings.embedding_cache = original
    embedding_cache.clear()


def test_disabled_flag_bypasses_cache(_restore_flag: None) -> None:
    embedding_cache.clear()
    settings.embedding_cache = False
    emb = _CountingEmbedder()
    embed_cached(emb, "z", tenant_id="t")
    embed_cached(emb, "z", tenant_id="t")
    assert emb.calls == 2  # no memoization when disabled
    assert embedding_cache.hit_rate == 0.0  # singleton untouched


def test_embed_batch_cached_disabled_still_batches(_restore_flag: None) -> None:
    embedding_cache.clear()
    settings.embedding_cache = False
    emb = _CountingEmbedder()
    vecs = embed_batch_cached(emb, ["a", "b", "a"], tenant_id="t")
    assert set(vecs) == {"a", "b"}
    assert emb.batch_calls == 1            # one provider call even with cache off
    assert embedding_cache.hit_rate == 0.0  # singleton untouched


# ── integration (DB) ──────────────────────────────────────
def test_recurring_entity_name_is_cached(db: StandardDatabase) -> None:
    embedding_cache.clear()
    hits: list[bool] = []
    metrics.on("embedding_cache", lambda **p: hits.append(p["hit"]))
    # "Zara" is embedded as an entity on both turns → the 2nd mention is a hit.
    store(db, content="Zara enters", turn_index=0, tenant_id="ec_int", agent_id="a")
    store(db, content="Zara returns", turn_index=1, tenant_id="ec_int", agent_id="a")
    metrics.clear()
    assert any(hits)  # at least one embedding cache hit occurred
