"""Integration tests for full-mode retrieval (adaptive gate + HyDE) (DESIGN.md §9)."""

from __future__ import annotations

from collections.abc import Callable

from arango.database import StandardDatabase

from arango_memory.generation import FakeGenerator
from arango_memory.ingest.store import store
from arango_memory.retrieve.enrich import QueryCache
from arango_memory.retrieve.search import RetrieveResult, retrieve


def _gate_then_hyde(gate_reply: str, hyde_reply: str) -> Callable[[str, str | None], str]:
    """Route the gate vs HyDE prompts by their system text."""

    def handler(prompt: str, system: str | None) -> str:
        return gate_reply if system and "SKIP" in system else hyde_reply

    return handler


def test_full_mode_adaptive_gate_skips_retrieval(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_gate", "agent_id": "a"}
    store(db, content="the user enjoys hiking in the alps", **ctx)

    gen = FakeGenerator(handler=_gate_then_hyde("SKIP", "irrelevant"))
    result = retrieve(db, query="what is 2 + 2", mode="full", generator=gen, **ctx)
    assert result.hits == []
    assert result.context == ""


def test_full_mode_hyde_retrieves(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_hyde", "agent_id": "a"}
    store(db, content="the user's favorite programming language is rust", **ctx)
    wait_for_searchable(db, query="favorite programming language", **ctx)

    gen = FakeGenerator(handler=_gate_then_hyde("RETRIEVE", "their favorite language is rust"))
    result = retrieve(db, query="which language do they prefer", mode="full", generator=gen, **ctx)
    assert result.hits
    assert any("rust" in h.text for h in result.hits)


def test_full_mode_caches_gate_and_hyde(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_cache", "agent_id": "a"}
    store(db, content="alpha bravo charlie delta", **ctx)
    wait_for_searchable(db, query="alpha bravo", **ctx)

    calls = {"n": 0}

    def handler(prompt: str, system: str | None) -> str:
        calls["n"] += 1
        return "RETRIEVE" if system and "SKIP" in system else "alpha bravo charlie"

    gen = FakeGenerator(handler=handler)
    cache = QueryCache()
    retrieve(db, query="alpha bravo", mode="full", generator=gen, cache=cache, **ctx)
    after_first = calls["n"]  # one gate + one HyDE call
    assert after_first == 2

    retrieve(db, query="alpha bravo", mode="full", generator=gen, cache=cache, **ctx)
    assert calls["n"] == after_first  # both served from cache
