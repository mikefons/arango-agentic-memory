"""Pluggable entity extraction (DESIGN.md §8 Stage 2).

Step 3a ships two implementations; GLiNER/GLiREL relation extraction and the
Haiku fallback are deferred to a later sub-step:
  - `FakeExtractor`  — deterministic capitalized-span heuristic; no models, used
                       by tests and the simulation harness.
  - `SpacyExtractor` — spaCy NER (behind the `extraction` extra).

Relations are not produced here; the store path derives `relates_to` edges from
co-occurrence (DESIGN.md §5). `get_extractor(settings)` selects an implementation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..config import Settings, settings

_CAP_SPAN = re.compile(r"\b[A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)*\b")

# Map spaCy entity labels → our §5 label set.
_SPACY_LABELS = {
    "PERSON": "Person",
    "ORG": "Organization",
    "GPE": "Location",
    "LOC": "Location",
    "FAC": "Location",
    "EVENT": "Event",
    "PRODUCT": "Object",
    "WORK_OF_ART": "Object",
}


@dataclass(frozen=True)
class ExtractedEntity:
    name: str
    label: str


@runtime_checkable
class Extractor(Protocol):
    name: str

    def extract(self, text: str) -> list[ExtractedEntity]: ...


class FakeExtractor:
    """Deterministic: capitalized spans → entities (label `Concept`). No models."""

    def __init__(self) -> None:
        self.name = "fake-caps"

    def extract(self, text: str) -> list[ExtractedEntity]:
        seen: dict[str, ExtractedEntity] = {}
        for match in _CAP_SPAN.findall(text):
            span = match.strip()
            if len(span) > 1 and span not in seen:
                seen[span] = ExtractedEntity(name=span, label="Concept")
        return list(seen.values())


class SpacyExtractor:
    """spaCy NER (behind the `extraction` extra)."""

    def __init__(self, model: str = "en_core_web_sm") -> None:
        try:
            import spacy
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError("SpacyExtractor needs the 'extraction' extra (spacy).") from exc
        self._nlp = spacy.load(model)
        self.name = f"spacy:{model}"

    def extract(self, text: str) -> list[ExtractedEntity]:
        seen: dict[tuple[str, str], ExtractedEntity] = {}
        for ent in self._nlp(text).ents:
            label = _SPACY_LABELS.get(ent.label_, "Concept")
            key = (ent.text, label)
            if key not in seen:
                seen[key] = ExtractedEntity(name=ent.text, label=label)
        return list(seen.values())


def get_extractor(config: Settings | None = None) -> Extractor:
    """Build the configured extractor."""
    cfg = config or settings
    if cfg.extraction_provider == "fake":
        return FakeExtractor()
    return SpacyExtractor(model=cfg.spacy_model)


def cooccurring_pairs(
    entities: Sequence[ExtractedEntity],
) -> list[tuple[ExtractedEntity, ExtractedEntity]]:
    """Unordered entity pairs that co-occur in one memory → `relates_to` edges (§5)."""
    pairs: list[tuple[ExtractedEntity, ExtractedEntity]] = []
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            pairs.append((entities[i], entities[j]))
    return pairs
