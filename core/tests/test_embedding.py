"""Unit tests for the pluggable embedder (no container)."""

from __future__ import annotations

import math

import pytest

from arango_memory.config import Settings
from arango_memory.embedding import (
    _EMBED_MAX_INPUTS,
    _EMBED_MAX_TOKENS,
    FakeEmbedder,
    OpenAIEmbedder,
    _truncate_to_token_limit,
    get_embedder,
)


def test_truncate_leaves_short_text_untouched() -> None:
    texts = ["hi", "a normal short memory turn"]
    assert _truncate_to_token_limit(texts) == texts  # no tokenizer cost, identity


def test_truncate_caps_overlong_text_to_token_limit() -> None:
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    long_text = "word " * 20000  # ~20k tokens, well over the 8192 limit
    out = _truncate_to_token_limit([long_text])[0]
    assert len(enc.encode(out)) <= _EMBED_MAX_TOKENS
    assert out != long_text and long_text.startswith(out[:20])  # a prefix, truncated


def test_fake_embedder_deterministic_and_unit_norm() -> None:
    emb = FakeEmbedder(dimensions=64)
    v1 = emb.embed("hello world")
    v2 = emb.embed("hello world")
    assert v1 == v2
    assert len(v1) == 64
    assert math.isclose(math.sqrt(sum(x * x for x in v1)), 1.0, abs_tol=1e-6)


def test_fake_embedder_lexical_similarity_signal() -> None:
    emb = FakeEmbedder(dimensions=128)

    def cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    base = emb.embed("the cat sat on the mat")
    near = emb.embed("the cat sat on the rug")
    far = emb.embed("quantum chromodynamics seminar")
    assert cos(base, near) > cos(base, far)


def test_fake_embedder_batch_matches_single() -> None:
    emb = FakeEmbedder(dimensions=32)
    assert emb.embed_batch(["a", "b"]) == [emb.embed("a"), emb.embed("b")]


def test_openai_embed_batch_chunks_over_input_cap() -> None:
    # The batched graph pass can hand more than 2048 distinct entity names at once; the OpenAI
    # endpoint rejects a request over 2048 inputs, so embed_batch must split into sub-requests
    # (and stitch the vectors back in order) rather than 400.
    emb = OpenAIEmbedder.__new__(OpenAIEmbedder)
    emb.model = "text-embedding-3-small"
    emb.version = emb.model
    emb.dimensions = 3
    sizes: list[int] = []

    class _Resp:
        def __init__(self, batch: list[str]) -> None:
            self.data = [type("D", (), {"embedding": [float(len(t)), 0.0, 0.0]}) for t in batch]

    class _Embeddings:
        def create(self, *, model: str, input: list[str]):  # noqa: A002 — matches SDK kwarg
            sizes.append(len(input))
            return _Resp(input)

    emb._client = type("C", (), {"embeddings": _Embeddings()})()

    n = _EMBED_MAX_INPUTS * 2 + 5
    out = emb.embed_batch([f"t{i}" for i in range(n)])
    assert len(out) == n  # every input got a vector, order preserved
    assert sizes == [_EMBED_MAX_INPUTS, _EMBED_MAX_INPUTS, 5]  # chunked under the cap
    assert max(sizes) <= _EMBED_MAX_INPUTS


def test_get_embedder_fake_from_config() -> None:
    emb = get_embedder(Settings(embedding_provider="fake", embedding_dimensions=48))
    assert emb.model == "fake-hash"
    assert emb.dimensions == 48


def test_get_embedder_openai_requires_key() -> None:
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_embedder(Settings(embedding_provider="openai", openai_api_key=None))
