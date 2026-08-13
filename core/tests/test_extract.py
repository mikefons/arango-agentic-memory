"""Unit tests for the pluggable extractor (no container)."""

from __future__ import annotations

from arango_memory.config import Settings
from arango_memory.ingest.extract import (
    ExtractedEntity,
    FakeExtractor,
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
