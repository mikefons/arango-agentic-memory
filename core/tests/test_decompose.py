"""Query decomposition (RQ-1): parse, dedupe, cap, and the single-shot fallback. No DB."""

from __future__ import annotations

from arango_memory.generation import FakeGenerator
from arango_memory.retrieve.decompose import decompose
from arango_memory.retrieve.enrich import QueryCache


def _gen(text: str) -> FakeGenerator:
    return FakeGenerator(handler=lambda prompt, system: text)


def test_splits_into_independent_subqueries() -> None:
    subs = decompose(
        "Where does the person Alice met at the reunion work?",
        generator=_gen("Who did Alice meet at the reunion?\nWhere does that person work?"),
    )
    assert subs == ["Who did Alice meet at the reunion?", "Where does that person work?"]


def test_strips_bullets_and_enumerators() -> None:
    subs = decompose("q", generator=_gen("1. First lookup\n- Second lookup\n* Third lookup"))
    assert subs == ["First lookup", "Second lookup", "Third lookup"]


def test_dedupes_case_insensitively_preserving_order() -> None:
    subs = decompose("q", generator=_gen("Where is Bob?\nwhere is bob?\nWho is Bob?"))
    assert subs == ["Where is Bob?", "Who is Bob?"]


def test_caps_at_max_subqueries() -> None:
    from arango_memory.config import settings

    lines = "\n".join(f"lookup {i}" for i in range(settings.decompose_max_subqueries + 3))
    subs = decompose("q", generator=_gen(lines))
    assert len(subs) == settings.decompose_max_subqueries
    assert subs[0] == "lookup 0"


def test_single_lookup_falls_back_to_original_query() -> None:
    # One separable lookup is not multi-hop — return the original query for single-shot.
    assert decompose("original q", generator=_gen("just one lookup")) == ["original q"]


def test_empty_output_falls_back_to_original_query() -> None:
    assert decompose("original q", generator=_gen("   \n\n")) == ["original q"]


def test_generator_failure_falls_back_to_original_query() -> None:
    def boom(prompt: str, system: str | None) -> str:
        raise RuntimeError("provider down")

    assert decompose("original q", generator=FakeGenerator(handler=boom)) == ["original q"]


def test_cache_short_circuits_the_second_call() -> None:
    calls = {"n": 0}

    def handler(prompt: str, system: str | None) -> str:
        calls["n"] += 1
        return "First lookup\nSecond lookup"

    cache = QueryCache()
    gen = FakeGenerator(handler=handler)
    first = decompose("q", generator=gen, cache=cache)
    second = decompose("q", generator=gen, cache=cache)
    assert first == second == ["First lookup", "Second lookup"]
    assert calls["n"] == 1  # second call served from cache
