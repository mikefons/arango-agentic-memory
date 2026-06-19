"""Dedicated embedding cache (DESIGN.md §16/§24).

A process-level LRU that memoizes `embed(text)` so recurring inputs — above all the
**entity names** re-embedded on every mention, plus repeated queries and idempotent
replays — skip the (paid, latency-bound) provider call. Distinct from the query
cache (`retrieve.enrich.QueryCache`), which memoizes HyDE/gate *LLM* results.

Keyed by `(tenant_id, model, version, dimensions, sha256(text))`: **per-tenant
namespacing** is the §24 timing-attack defense — a cache hit can't reveal that
another tenant embedded the same text. `dimensions` is in the key so two embedders
of the same model but different vector size never collide.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

from .config import settings
from .embedding import Embedder
from .telemetry import metrics

# Bound shared-cache growth: embeddings are deterministic per (model, version, text),
# so a long TTL is safe and lets stale entries lapse (config Redis maxmemory-policy
# = allkeys-lru for a hard cap). 30 days.
_REDIS_TTL_SECONDS = 30 * 24 * 3600


class EmbeddingCache:
    """In-process LRU for embedding vectors, keyed + namespaced per tenant."""

    def __init__(self, max_size: int = 10000) -> None:
        self._store: OrderedDict[str, list[float]] = OrderedDict()
        self._max_size = max_size
        self._hits = 0
        self._lookups = 0

    @property
    def hit_rate(self) -> float:
        return self._hits / self._lookups if self._lookups else 0.0

    @staticmethod
    def _key(embedder: Embedder, text: str, tenant_id: str) -> str:
        raw = (
            f"{tenant_id}\x1f{embedder.model}\x1f{embedder.version}"
            f"\x1f{embedder.dimensions}\x1f{text}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def embed(self, embedder: Embedder, text: str, *, tenant_id: str) -> list[float]:
        key = self._key(embedder, text, tenant_id)
        self._lookups += 1
        cached = self._store.get(key)
        if cached is not None:
            self._hits += 1
            self._store.move_to_end(key)  # LRU bump
            metrics.emit("embedding_cache", hit=True, hit_rate=self.hit_rate)
            return cached

        vec = embedder.embed(text)
        self._store[key] = vec
        if len(self._store) > self._max_size:
            self._store.popitem(last=False)  # evict least-recently-used
        metrics.emit("embedding_cache", hit=False, hit_rate=self.hit_rate)
        return vec

    def _store_miss(self, key: str, vec: list[float]) -> None:
        self._store[key] = vec
        if len(self._store) > self._max_size:
            self._store.popitem(last=False)  # evict least-recently-used
        metrics.emit("embedding_cache", hit=False, hit_rate=self.hit_rate)

    def embed_many(
        self, embedder: Embedder, texts: Sequence[str], *, tenant_id: str
    ) -> dict[str, list[float]]:
        """Batch-embed distinct texts, serving cache hits and embedding misses in one
        provider call (`embed_batch`). Returns vec-by-text; preserves the per-text
        hit/miss metric so cache stats match the one-at-a-time path."""
        result: dict[str, list[float]] = {}
        misses: list[str] = []
        for text in dict.fromkeys(texts):  # dedupe, preserve order
            key = self._key(embedder, text, tenant_id)
            self._lookups += 1
            cached = self._store.get(key)
            if cached is not None:
                self._hits += 1
                self._store.move_to_end(key)  # LRU bump
                metrics.emit("embedding_cache", hit=True, hit_rate=self.hit_rate)
                result[text] = cached
            else:
                misses.append(text)
        if misses:
            for text, vec in zip(misses, embedder.embed_batch(misses), strict=True):
                self._store_miss(self._key(embedder, text, tenant_id), vec)
                result[text] = vec
        return result

    def clear(self) -> None:
        self._store.clear()
        self._hits = 0
        self._lookups = 0


class RedisEmbeddingCache:
    """Cross-instance embedding cache backed by Redis (DESIGN.md §16).

    Same key (so the §24 per-tenant namespacing holds) and per-text hit/miss metric
    as the in-process LRU, but the store is shared across instances. **Fail-soft:** a
    Redis error is treated as a miss (embed directly, skip caching) — never an error.
    Vectors are JSON-encoded; entries lapse via a long TTL.
    """

    _PREFIX = "emb:"

    def __init__(self) -> None:
        self._hits = 0
        self._lookups = 0

    @property
    def hit_rate(self) -> float:
        return self._hits / self._lookups if self._lookups else 0.0

    def _redis_key(self, embedder: Embedder, text: str, tenant_id: str) -> str:
        return self._PREFIX + EmbeddingCache._key(embedder, text, tenant_id)

    def _get(self, keys: list[str]) -> list[Any]:
        from .redis_client import get_redis

        return list(get_redis().mget(keys)) if keys else []

    def _put(self, key: str, vec: list[float]) -> None:
        from .redis_client import get_redis

        get_redis().set(key, json.dumps(vec), ex=_REDIS_TTL_SECONDS)

    def embed(self, embedder: Embedder, text: str, *, tenant_id: str) -> list[float]:
        return self.embed_many(embedder, [text], tenant_id=tenant_id)[text]

    def embed_many(
        self, embedder: Embedder, texts: Sequence[str], *, tenant_id: str
    ) -> dict[str, list[float]]:
        distinct = list(dict.fromkeys(texts))
        keys = [self._redis_key(embedder, t, tenant_id) for t in distinct]
        try:
            raws = self._get(keys)
        except Exception:  # noqa: BLE001 — §15 fail-soft: redis down → all-miss, no cache
            raws = [None] * len(distinct)

        result: dict[str, list[float]] = {}
        misses: list[str] = []
        for text, raw in zip(distinct, raws, strict=True):
            self._lookups += 1
            if raw is not None:
                self._hits += 1
                metrics.emit("embedding_cache", hit=True, hit_rate=self.hit_rate)
                result[text] = json.loads(raw)
            else:
                misses.append(text)

        if misses:
            for text, vec in zip(misses, embedder.embed_batch(misses), strict=True):
                with contextlib.suppress(Exception):  # fail-soft on write too
                    self._put(self._redis_key(embedder, text, tenant_id), vec)
                metrics.emit("embedding_cache", hit=False, hit_rate=self.hit_rate)
                result[text] = vec
        return result

    def clear(self) -> None:
        # Reset local counters only — never flush the shared store from a process.
        self._hits = 0
        self._lookups = 0


# Process-level singletons. The in-process LRU is the default; the Redis cache is
# selected when `REDIS_URL` is set (shared across instances). A worker/server
# accumulates hits across turns.
embedding_cache = EmbeddingCache(max_size=settings.embedding_cache_size)
redis_embedding_cache = RedisEmbeddingCache()


def _active_cache() -> EmbeddingCache | RedisEmbeddingCache:
    return redis_embedding_cache if settings.redis_url else embedding_cache


def embed_cached(embedder: Embedder, text: str, *, tenant_id: str) -> list[float]:
    """Embed via the shared cache when enabled; otherwise embed directly."""
    if not settings.embedding_cache:
        return embedder.embed(text)
    return _active_cache().embed(embedder, text, tenant_id=tenant_id)


def embed_batch_cached(
    embedder: Embedder, texts: Sequence[str], *, tenant_id: str
) -> dict[str, list[float]]:
    """Batch variant of `embed_cached`: one `embed_batch` call covers all distinct
    cache-misses. Returns vec-by-text."""
    if not settings.embedding_cache:
        distinct = list(dict.fromkeys(texts))
        return dict(zip(distinct, embedder.embed_batch(distinct), strict=True))
    return _active_cache().embed_many(embedder, texts, tenant_id=tenant_id)
