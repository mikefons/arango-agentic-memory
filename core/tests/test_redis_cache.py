"""Optional Redis-backed embedding cache (DESIGN.md §16). Uses fakeredis — no server."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import fakeredis
import pytest

import arango_memory.embedding_cache as ec
import arango_memory.redis_client as redis_client
from arango_memory.config import settings
from arango_memory.embedding import FakeEmbedder


class _CountingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__(dimensions=32)
        self.calls = 0
        self.batch_calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        return super().embed(text)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        self.batch_calls += 1
        base = FakeEmbedder.embed
        return [base(self, t) for t in texts]


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[fakeredis.FakeRedis]:
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(settings, "redis_url", "redis://fake")
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    yield fake


def test_active_cache_selects_by_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "redis_url", None)
    assert isinstance(ec._active_cache(), ec.EmbeddingCache)
    monkeypatch.setattr(settings, "redis_url", "redis://fake")
    assert isinstance(ec._active_cache(), ec.RedisEmbeddingCache)


def test_hit_is_shared_across_instances(fake_redis: Any) -> None:
    emb = _CountingEmbedder()
    # One instance populates Redis; a *different* cache object (= another API
    # instance) then serves the same text from the shared store — no second embed.
    v1 = ec.RedisEmbeddingCache().embed(emb, "Zara", tenant_id="t")
    assert emb.batch_calls == 1
    v2 = ec.RedisEmbeddingCache().embed(emb, "Zara", tenant_id="t")
    assert v2 == v1
    assert emb.batch_calls == 1  # second instance hit the shared cache


def test_per_tenant_namespacing(fake_redis: Any) -> None:
    emb = _CountingEmbedder()
    cache = ec.RedisEmbeddingCache()
    cache.embed(emb, "secret", tenant_id="t1")
    cache.embed(emb, "secret", tenant_id="t2")  # different tenant → distinct key → miss
    assert emb.batch_calls == 2  # no cross-tenant hit (§24)


def test_embed_many_batches_misses_once(fake_redis: Any) -> None:
    emb = _CountingEmbedder()
    cache = ec.RedisEmbeddingCache()
    cache.embed(emb, "a", tenant_id="t")  # warm "a"
    vecs = cache.embed_many(emb, ["a", "b", "c"], tenant_id="t")
    assert set(vecs) == {"a", "b", "c"}
    assert emb.batch_calls == 2  # first warm + one batch for {b,c}; "a" served from Redis


def test_fail_soft_on_redis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "redis_url", "redis://fake")

    def boom() -> Any:
        raise RuntimeError("redis down")

    monkeypatch.setattr(redis_client, "get_redis", boom)
    emb = _CountingEmbedder()
    # Redis unreachable → still returns a vector (computed directly), never raises.
    vec = ec.RedisEmbeddingCache().embed(emb, "x", tenant_id="t")
    assert len(vec) == 32 and emb.batch_calls == 1


def test_embed_cached_routes_through_redis(fake_redis: Any) -> None:
    # The public helper picks the Redis backend when REDIS_URL is set.
    emb = _CountingEmbedder()
    ec.embed_cached(emb, "routed", tenant_id="t")
    raw = fake_redis.get(ec.RedisEmbeddingCache()._redis_key(emb, "routed", "t"))
    assert raw is not None  # landed in the shared store
