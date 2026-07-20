"""Pluggable cross-encoder reranking (RQ-2b, DESIGN.md §9).

A reranker re-scores the fused candidate pool by *joint* (query, passage) relevance —
the fix the RQ-2a diagnostic pointed to (misses are in-pool but ranked below top-k, §23).
Sync, like the Embedder/Generator protocols.

Two implementations:
  - `FakeReranker` — deterministic Jaccard token-overlap of query and passage; no model,
    keyless. Shared tokens → higher score, a crude but real relevance signal so tests/CI
    exercise the rerank path offline.
  - `LocalCrossEncoderReranker` — a sentence-transformers CrossEncoder (default
    `BAAI/bge-reranker-base`), the standard two-stage-retrieval reranker.

`get_reranker(settings)` selects one from config; selecting "local" without the optional
`rerank` extra installed is a hard error (no silent degradation to the fake reranker).
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol, runtime_checkable

from ..config import Settings, settings


@runtime_checkable
class Reranker(Protocol):
    model: str

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        """Relevance score per passage (higher = more relevant); order matches `texts`."""
        ...


def _tokens(text: str) -> set[str]:
    return set("".join(c.lower() if c.isalnum() else " " for c in text).split())


class FakeReranker:
    """Deterministic reranker: fraction of the query's tokens the passage covers. Keyless.

    Coverage (`|q ∩ d| / |q|`) rather than Jaccard, so a passage that addresses more of the
    query ranks higher regardless of its length — a crude but sensible relevance proxy.
    """

    def __init__(self) -> None:
        self.model = "fake-rerank"

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        q = _tokens(query)
        if not q:
            return [0.0] * len(texts)
        return [len(q & _tokens(text)) / len(q) for text in texts]


class LocalCrossEncoderReranker:
    """Cross-encoder relevance scoring via sentence-transformers (default bge-reranker-base)."""

    def __init__(self, model: str = "BAAI/bge-reranker-base") -> None:
        from sentence_transformers import CrossEncoder

        self._encoder = CrossEncoder(model)
        self.model = model

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        pairs = [(query, text) for text in texts]
        return [float(s) for s in self._encoder.predict(pairs)]


@lru_cache(maxsize=4)
def _build_reranker(provider: str, model: str) -> Reranker:
    """Construct a reranker, cached by (provider, model). The local cross-encoder loads a
    ~1GB model, so callers that rerank many queries (e.g. the benchmark) build it once and
    reuse it instead of reloading per call. Exceptions are not cached — a failed build
    retries next time."""
    if provider == "fake":
        return FakeReranker()
    try:
        return LocalCrossEncoderReranker(model=model)
    except ImportError as exc:  # optional 'rerank' extra not installed
        raise RuntimeError(
            "reranker_provider='local' needs the 'rerank' extra (sentence-transformers); "
            "install `arango-memory[rerank]` or use reranker_provider='fake'."
        ) from exc


def get_reranker(config: Settings | None = None) -> Reranker:
    """The configured reranker (cached per provider+model). Selecting 'local' without the
    extra is an error."""
    cfg = config or settings
    return _build_reranker(cfg.reranker_provider, cfg.reranker_model)
