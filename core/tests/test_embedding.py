"""Unit tests for the pluggable embedder (no container)."""

from __future__ import annotations

import math

import pytest

from arango_memory.config import Settings
from arango_memory.embedding import FakeEmbedder, get_embedder


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


def test_get_embedder_fake_from_config() -> None:
    emb = get_embedder(Settings(embedding_provider="fake", embedding_dimensions=48))
    assert emb.model == "fake-hash"
    assert emb.dimensions == 48


def test_get_embedder_openai_requires_key() -> None:
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_embedder(Settings(embedding_provider="openai", openai_api_key=None))
