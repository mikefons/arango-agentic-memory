"""Unit tests for full-mode enrichment: adaptive gate, HyDE, caching (no container)."""

from __future__ import annotations

from arango_memory.embedding import FakeEmbedder
from arango_memory.generation import FakeGenerator
from arango_memory.retrieve.enrich import QueryCache, hyde, should_skip_retrieval


def test_gate_skip_vs_retrieve() -> None:
    skip = FakeGenerator(handler=lambda p, s: "SKIP")
    keep = FakeGenerator(handler=lambda p, s: "RETRIEVE")
    assert should_skip_retrieval("q", generator=skip) is True
    assert should_skip_retrieval("q", generator=keep) is False


def test_gate_default_retrieves() -> None:
    assert should_skip_retrieval("q", generator=FakeGenerator()) is False


def test_hyde_embeds_the_generated_hypothetical() -> None:
    emb = FakeEmbedder(dimensions=32)
    gen = FakeGenerator(handler=lambda p, s: "a hypothetical answer about cats")
    result = hyde("tell me about cats", generator=gen, embedder=emb)
    assert result.hypothetical == "a hypothetical answer about cats"
    assert result.embedding == emb.embed("a hypothetical answer about cats")


def test_hyde_falls_back_to_raw_query_when_empty() -> None:
    emb = FakeEmbedder(dimensions=32)
    result = hyde("raw query", generator=FakeGenerator(), embedder=emb)
    assert result.hypothetical == "raw query"
    assert result.embedding == emb.embed("raw query")


def test_gate_cache_avoids_second_generation() -> None:
    calls = {"n": 0}

    def handler(prompt: str, system: str | None) -> str:
        calls["n"] += 1
        return "SKIP"

    gen = FakeGenerator(handler=handler)
    cache = QueryCache()
    assert should_skip_retrieval("q", generator=gen, cache=cache) is True
    assert should_skip_retrieval("q", generator=gen, cache=cache) is True
    assert calls["n"] == 1


def test_hyde_cache_avoids_second_generation() -> None:
    emb = FakeEmbedder(dimensions=16)
    calls = {"n": 0}

    def handler(prompt: str, system: str | None) -> str:
        calls["n"] += 1
        return "hypothetical"

    gen = FakeGenerator(handler=handler)
    cache = QueryCache()
    first = hyde("q", generator=gen, embedder=emb, cache=cache)
    second = hyde("q", generator=gen, embedder=emb, cache=cache)
    assert first == second
    assert calls["n"] == 1
