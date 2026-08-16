"""Unit tests for the pluggable extractor (no container)."""

from __future__ import annotations

from arango_memory.config import Settings
from arango_memory.generation import FakeGenerator
from arango_memory.ingest.extract import (
    ExtractedEntity,
    FakeExtractor,
    HaikuExtractor,
    cooccurring_pairs,
    get_extractor,
)


def test_fake_extractor_pulls_capitalized_spans() -> None:
    ents = FakeExtractor().extract("Alice met Bob Smith in Paris yesterday")
    assert {e.name for e in ents} == {"Alice", "Bob Smith", "Paris"}
    assert all(e.label == "Concept" for e in ents)


def test_fake_extractor_dedupes_deterministically() -> None:
    ents = FakeExtractor().extract("Acme grew. Acme grew again.")
    assert [e.name for e in ents] == ["Acme"]


def test_cooccurring_pairs_are_unordered_combinations() -> None:
    ents = [ExtractedEntity("A", "X"), ExtractedEntity("B", "X"), ExtractedEntity("C", "X")]
    pairs = cooccurring_pairs(ents)
    assert len(pairs) == 3  # C(3,2)


def test_cooccurring_pairs_cap_bounds_dense_turns() -> None:
    # 8 entities → C(8,2)=28 pairs uncapped; the cap bounds the all-pairs blow-up (IN-3).
    ents = [ExtractedEntity(name, "X") for name in "ABCDEFGH"]
    assert len(cooccurring_pairs(ents)) == 28  # uncapped default unchanged
    assert len(cooccurring_pairs(ents, max_pairs=10)) == 10  # capped
    # a small turn is under any sane cap → untouched
    small = [ExtractedEntity("A", "X"), ExtractedEntity("B", "X")]
    assert cooccurring_pairs(small, max_pairs=32) == cooccurring_pairs(small)


def test_get_extractor_fake_from_config() -> None:
    assert get_extractor(Settings(extraction_provider="fake")).name == "fake-caps"


def test_haiku_extractor_caches_per_text(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # IN-7: the cache is a per-text dict (not a single slot) so `extract` + `extract_relations`
    # for one memory share ONE (paid) LLM call, and an earlier text stays cached when a later
    # one is processed — the property that keeps concurrent extraction from doubling calls.
    calls: list[str] = []

    def handler(prompt: str, system: str | None) -> str:
        calls.append(prompt)
        return '{"entities": [{"name": "Mira", "label": "Person"}], "relations": []}'

    ex = HaikuExtractor(generator=FakeGenerator(handler=handler))

    ents = ex.extract("Mira is my sister")
    ex.extract_relations("Mira is my sister", ents)  # same text → cache hit, no 2nd call
    assert [e.name for e in ents] == ["Mira"]
    assert len(calls) == 1

    ex.extract("I moved to Berlin")                  # new text → 2nd call
    assert len(calls) == 2

    ex.extract("Mira is my sister")                  # first text still cached (dict, not a slot)
    assert len(calls) == 2
