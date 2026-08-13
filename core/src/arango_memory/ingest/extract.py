"""Pluggable entity + relation extraction (DESIGN.md §8 Stage 2).

The §8 Stage 2 tiers, all behind the `Extractor` Protocol:
  - `FakeExtractor`   — deterministic capitalized-span heuristic; no models, used
                        by tests and the simulation harness.
  - `SpacyExtractor`  — spaCy NER (tier A; behind the `extraction` extra).
  - `GlinerExtractor` — GLiNER zero-shot NER + GLiREL typed relations (tier B;
                        `extraction` extra; torch — kept out of CI).
  - `HaikuExtractor`  — LLM extraction via a `Generator` (tier C; keyless in CI
                        via `FakeGenerator`).
  - `LayeredExtractor`— the A→B→C chain: spaCy, then GLiNER fill, escalating to
                        Haiku only on empty/ambiguous turns.

An extractor may also produce **typed relations** (`extract_relations`); the
store path writes those `relates_to` edges, falling back to co-occurrence
`associated_with` for pairs no extractor typed (DESIGN.md §5). `get_extractor`
selects an implementation from settings.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..config import Settings, settings

_CAP_SPAN = re.compile(r"\b[A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)*\b")

# The §5 typed-relation enum. GLiREL/Haiku labels are coerced into this set; an
# unrecognized label degrades to the generic `associated_with`.
RELATION_LABELS = ("caused_by", "occurred_during", "subtopic_of", "associated_with")
DEFAULT_RELATION = "associated_with"


def coerce_relation(label: str) -> str:
    """Map a raw relation label to the §5 enum (generic fallback otherwise)."""
    norm = label.strip().lower().replace(" ", "_").replace("-", "_")
    return norm if norm in RELATION_LABELS else DEFAULT_RELATION

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


@dataclass(frozen=True)
class ExtractedRelation:
    source: str  # entity name
    target: str  # entity name
    relationship: str  # one of RELATION_LABELS


@runtime_checkable
class Extractor(Protocol):
    name: str

    def extract(self, text: str) -> list[ExtractedEntity]: ...

    def extract_relations(
        self, text: str, entities: Sequence[ExtractedEntity]
    ) -> list[ExtractedRelation]: ...


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

    def extract_relations(
        self, text: str, entities: Sequence[ExtractedEntity]
    ) -> list[ExtractedRelation]:
        return []  # untyped: store path falls back to co-occurrence


class SpacyExtractor:
    """spaCy NER (tier A; behind the `extraction` extra). No typed relations."""

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

    def extract_relations(
        self, text: str, entities: Sequence[ExtractedEntity]
    ) -> list[ExtractedRelation]:
        return []


def _dedupe_entities(entities: Iterable[ExtractedEntity]) -> list[ExtractedEntity]:
    seen: dict[tuple[str, str], ExtractedEntity] = {}
    for ent in entities:
        seen.setdefault((ent.name, ent.label), ent)
    return list(seen.values())


# Injectable seams so the torch/LLM tiers are testable without torch or a key.
RelationFn = Callable[[str, list[str], list[str]], list[tuple[str, str, str]]]


class GlinerExtractor:
    """Tier B — GLiNER zero-shot NER + GLiREL typed relations (the `extraction`
    extra; torch, kept out of CI). The NER model and relation function are
    injectable so the logic is testable with deterministic fakes."""

    def __init__(
        self,
        model: str = "urchade/gliner_mediumv2.1",
        *,
        ner: Any | None = None,
        relation_fn: RelationFn | None = None,
        entity_labels: Sequence[str] = (),
        relation_labels: Sequence[str] = (),
        threshold: float = 0.5,
    ) -> None:
        self.name = f"gliner:{model}"
        self._ner = ner or _load_gliner(model)
        self._relation_fn = relation_fn or _load_glirel()
        self.entity_labels = list(entity_labels) or ["Person", "Organization", "Location"]
        self.relation_labels = list(relation_labels) or list(RELATION_LABELS)
        self.threshold = threshold

    def extract(self, text: str) -> list[ExtractedEntity]:
        spans = self._ner.predict_entities(text, self.entity_labels, threshold=self.threshold)
        return _dedupe_entities(
            ExtractedEntity(name=s["text"], label=s.get("label", "Concept")) for s in spans
        )

    def extract_relations(
        self, text: str, entities: Sequence[ExtractedEntity]
    ) -> list[ExtractedRelation]:
        names = [e.name for e in entities]
        return [
            ExtractedRelation(src, tgt, coerce_relation(rel))
            for src, tgt, rel in self._relation_fn(text, names, self.relation_labels)
            if src != tgt
        ]


_HAIKU_SYSTEM = (
    "You extract a knowledge graph from text. Return ONLY JSON of the form "
    '{{"entities": [{{"name": str, "label": str}}], '
    '"relations": [{{"source": str, "target": str, "relationship": str}}]}}. '
    "Use these entity labels: {labels}. Use these relationship labels: {relations}. "
    "Names in relations must match extracted entity names. No prose, no code fences."
)


class HaikuExtractor:
    """Tier C — LLM extraction via a `Generator` (keyless in CI via `FakeGenerator`).
    One LLM call per text serves both `extract` and `extract_relations` (cached)."""

    def __init__(
        self,
        generator: Any | None = None,
        *,
        entity_labels: Sequence[str] = (),
        relation_labels: Sequence[str] = (),
        max_tokens: int = 512,
    ) -> None:
        self.name = "haiku"
        self._gen = generator
        self.entity_labels = list(entity_labels) or [
            "Person", "Organization", "Location", "Event", "Object", "Concept",
        ]
        self.relation_labels = list(relation_labels) or list(RELATION_LABELS)
        self.max_tokens = max_tokens
        self._cache: tuple[str, dict[str, Any]] | None = None

    def _generator(self) -> Any:
        if self._gen is None:
            from ..generation import get_generator

            self._gen = get_generator()
        return self._gen

    def _call(self, text: str) -> dict[str, Any]:
        if self._cache and self._cache[0] == text:
            return self._cache[1]
        system = _HAIKU_SYSTEM.format(
            labels=", ".join(self.entity_labels), relations=", ".join(self.relation_labels)
        )
        raw = self._generator().complete(text, system=system, max_tokens=self.max_tokens)
        data = _parse_json_object(raw)
        self._cache = (text, data)
        return data

    def extract(self, text: str) -> list[ExtractedEntity]:
        data = self._call(text)
        ents = (
            ExtractedEntity(name=str(e["name"]), label=str(e.get("label", "Concept")))
            for e in data.get("entities", [])
            if isinstance(e, dict) and e.get("name")
        )
        return _dedupe_entities(ents)

    def extract_relations(
        self, text: str, entities: Sequence[ExtractedEntity]
    ) -> list[ExtractedRelation]:
        data = self._call(text)
        out: list[ExtractedRelation] = []
        for r in data.get("relations", []):
            if isinstance(r, dict) and r.get("source") and r.get("target"):
                src, tgt = str(r["source"]), str(r["target"])
                if src != tgt:
                    out.append(
                        ExtractedRelation(src, tgt, coerce_relation(str(r.get("relationship", ""))))
                    )
        return out


class LayeredExtractor:
    """The §8 Stage 2 A→B→C chain: a base NER tier (spaCy/fake), a GLiNER fill,
    and a Haiku tier that fires only when the cheaper tiers come up short."""

    def __init__(
        self,
        base: Extractor,
        gliner: Extractor | None = None,
        haiku: Extractor | None = None,
        *,
        escalate_below: int = 1,
    ) -> None:
        self.name = "layered"
        self.base = base
        self.gliner = gliner
        self.haiku = haiku
        self.escalate_below = escalate_below

    def _cheap_entities(self, text: str) -> list[ExtractedEntity]:
        ents = list(self.base.extract(text))
        if self.gliner is not None:
            ents += self.gliner.extract(text)
        return _dedupe_entities(ents)

    def extract(self, text: str) -> list[ExtractedEntity]:
        ents = self._cheap_entities(text)
        if self.haiku is not None and len(ents) < self.escalate_below:
            ents = _dedupe_entities([*ents, *self.haiku.extract(text)])
        return ents

    def extract_relations(
        self, text: str, entities: Sequence[ExtractedEntity]
    ) -> list[ExtractedRelation]:
        rels: list[ExtractedRelation] = []
        if self.gliner is not None:
            rels += self.gliner.extract_relations(text, entities)
        if not rels and self.haiku is not None:
            rels += self.haiku.extract_relations(text, entities)
        seen: dict[tuple[str, str, str], ExtractedRelation] = {}
        for r in rels:
            seen.setdefault((r.source, r.target, r.relationship), r)
        return list(seen.values())


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Best-effort parse of a JSON object from an LLM response (tolerates fences)."""
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :] if "{" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _load_gliner(model: str) -> Any:  # pragma: no cover - needs the extra + torch
    try:
        from gliner import GLiNER
    except ImportError as exc:
        raise RuntimeError("GlinerExtractor needs the 'extraction' extra (gliner).") from exc
    return GLiNER.from_pretrained(model)


def _load_glirel() -> RelationFn:  # pragma: no cover - needs the extra + torch
    try:
        from glirel import GLiREL
    except ImportError as exc:
        raise RuntimeError("GLiREL relations need the 'extraction' extra (glirel).") from exc
    model = GLiREL.from_pretrained("jackboyla/glirel-large-v0")

    def _fn(text: str, names: list[str], labels: list[str]) -> list[tuple[str, str, str]]:
        tokens = text.split()
        results = model.predict_relations(tokens, labels, threshold=0.0, ner=names, top_k=1)
        return [(r["head_text"], r["tail_text"], r["label"]) for r in results]

    return _fn


def get_extractor(config: Settings | None = None) -> Extractor:
    """Build the configured extractor (DESIGN.md §8 Stage 2)."""
    cfg = config or settings
    provider = cfg.extraction_provider
    if provider == "fake":
        return FakeExtractor()
    if provider == "spacy":
        return SpacyExtractor(model=cfg.spacy_model)
    if provider == "gliner":
        return GlinerExtractor(
            model=cfg.gliner_model,
            entity_labels=cfg.gliner_entity_labels,
            relation_labels=cfg.relation_labels,
        )
    if provider == "haiku":
        return HaikuExtractor(
            entity_labels=cfg.gliner_entity_labels, relation_labels=cfg.relation_labels
        )
    return LayeredExtractor(
        base=SpacyExtractor(model=cfg.spacy_model),
        gliner=GlinerExtractor(
            model=cfg.gliner_model,
            entity_labels=cfg.gliner_entity_labels,
            relation_labels=cfg.relation_labels,
        ),
        haiku=HaikuExtractor(
            entity_labels=cfg.gliner_entity_labels, relation_labels=cfg.relation_labels
        ),
        escalate_below=cfg.extraction_escalate_below,
    )


def cooccurring_pairs(
    entities: Sequence[ExtractedEntity], *, max_pairs: int | None = None,
) -> list[tuple[ExtractedEntity, ExtractedEntity]]:
    """Unordered entity pairs that co-occur in one memory → `relates_to` edges (§5).

    `max_pairs` (IN-3) bounds the all-pairs blow-up: a turn with E entities otherwise yields
    E(E−1)/2 pairs, which is both an ingestion cost and graph noise. When set, at most
    `max_pairs` pairs are generated (early return), so dense turns are down-sampled rather than
    minting hundreds of low-signal `associated_with` edges. Ordinary turns (small E) are
    unaffected."""
    pairs: list[tuple[ExtractedEntity, ExtractedEntity]] = []
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            pairs.append((entities[i], entities[j]))
            if max_pairs is not None and len(pairs) >= max_pairs:
                return pairs
    return pairs
