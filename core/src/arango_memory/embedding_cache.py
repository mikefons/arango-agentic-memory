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

import hashlib
from collections import OrderedDict
from collections.abc import Sequence

from .config import settings
from .embedding import Embedder
from .telemetry import metrics


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


# Process-level singleton — a long-lived worker/server accumulates hits across turns.
embedding_cache = EmbeddingCache(max_size=settings.embedding_cache_size)


def embed_cached(embedder: Embedder, text: str, *, tenant_id: str) -> list[float]:
    """Embed via the shared cache when enabled; otherwise embed directly."""
    if not settings.embedding_cache:
        return embedder.embed(text)
    return embedding_cache.embed(embedder, text, tenant_id=tenant_id)


def embed_batch_cached(
    embedder: Embedder, texts: Sequence[str], *, tenant_id: str
) -> dict[str, list[float]]:
    """Batch variant of `embed_cached`: one `embed_batch` call covers all distinct
    cache-misses. Returns vec-by-text."""
    if not settings.embedding_cache:
        distinct = list(dict.fromkeys(texts))
        return dict(zip(distinct, embedder.embed_batch(distinct), strict=True))
    return embedding_cache.embed_many(embedder, texts, tenant_id=tenant_id)
