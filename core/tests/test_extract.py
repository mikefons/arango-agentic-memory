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


def test_get_extractor_fake_from_config() -> None:
    assert get_extractor(Settings(extraction_provider="fake")).name == "fake-caps"
