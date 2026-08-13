"""Pluggable text embedding (DESIGN.md §8).

The core write/read path is synchronous (sync `python-arango`), so the Embedder
protocol is sync too — the async sketch in §8 is deferred until the durable
write path (Step 3) moves embedding off the request hot path.

Two implementations:
  - `FakeEmbedder`  — deterministic signed-hashing vectorizer; no API key, used
                      by tests and the deterministic simulation harness. Shared
                      tokens → higher cosine, so it gives a real (if crude)
                      lexical-similarity signal for exercising vector search.
  - `OpenAIEmbedder`— real embeddings via `text-embedding-3-small` (1536 dims).

`get_embedder(settings)` selects one from config; selecting "openai" without a
key is a hard error (no silent degradation to fake embeddings in production).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from .config import Settings, settings


@runtime_checkable
class Embedder(Protocol):
    model: str
    version: str
    dimensions: int

    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]: ...


def _tokenize(text: str) -> list[str]:
    return "".join(c.lower() if c.isalnum() else " " for c in text).split()


class FakeEmbedder:
    """Deterministic signed-hashing vectorizer. Same text → same unit vector."""

    def __init__(self, dimensions: int = 256) -> None:
        self.model = "fake-hash"
        self.version = "1"
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for tok in _tokenize(text):
            digest = hashlib.sha1(tok.encode()).digest()  # noqa: S324 — non-crypto use
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            vec[idx] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


# text-embedding-3-* reject inputs over 8192 tokens with a 400; truncate to fit (an
# over-long memory still embeds on its first 8k tokens rather than crashing the write).
_EMBED_MAX_TOKENS = 8192
# …and reject a request carrying more than 2048 inputs with a 400. The batched graph pass
# (write_entities_many) can hand a whole question's distinct entity names in one call — well
# over 2048 on a 500-turn history — so chunk the request into sub-batches under the cap.
_EMBED_MAX_INPUTS = 2048
_embed_encoder: Any = None


def _truncate_to_token_limit(texts: Sequence[str]) -> list[str]:
    """Truncate any input over the embedding model's token limit. Only tokenizes texts long
    enough to possibly exceed it (token count ≤ char count for ordinary text), so short turns
    pay no tokenizer cost."""
    global _embed_encoder
    out: list[str] = []
    for text in texts:
        if len(text) <= _EMBED_MAX_TOKENS:
            out.append(text)
            continue
        if _embed_encoder is None:
            import tiktoken

            _embed_encoder = tiktoken.get_encoding("cl100k_base")
        tokens = _embed_encoder.encode(text)
        out.append(
            _embed_encoder.decode(tokens[:_EMBED_MAX_TOKENS])
            if len(tokens) > _EMBED_MAX_TOKENS
            else text
        )
    return out


class OpenAIEmbedder:
    """Real embeddings via the OpenAI API (default `text-embedding-3-small`)."""

    _DIMENSIONS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}

    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self.model = model
        self.version = model
        self.dimensions = self._DIMENSIONS.get(model, 1536)

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        inputs = _truncate_to_token_limit(list(texts))
        vectors: list[list[float]] = []
        for start in range(0, len(inputs), _EMBED_MAX_INPUTS):
            resp = self._client.embeddings.create(
                model=self.model, input=inputs[start : start + _EMBED_MAX_INPUTS]
            )
            vectors.extend(item.embedding for item in resp.data)
        return vectors


def get_embedder(config: Settings | None = None) -> Embedder:
    """Build the configured embedder. Selecting OpenAI without a key is an error."""
    cfg = config or settings
    if cfg.embedding_provider == "fake":
        return FakeEmbedder(dimensions=cfg.embedding_dimensions)
    if not cfg.openai_api_key:
        raise RuntimeError(
            "embedding_provider='openai' but OPENAI_API_KEY is unset; "
            "set the key or use embedding_provider='fake'."
        )
    return OpenAIEmbedder(api_key=cfg.openai_api_key, model=cfg.embedding_model)
