"""Heavy extraction tier — GLiNER/Haiku/Layered + typed relations + valid_time.

Keyless and torch-free (DESIGN.md §8 Stage 2): the GLiNER NER model and GLiREL
relation function are injected as deterministic fakes; the Haiku tier runs on
`FakeGenerator`. No real models or API keys are touched.
"""

from __future__ import annotations

from collections.abc import Sequence

from arango.database import StandardDatabase

from arango_memory.embedding import get_embedder
from arango_memory.generation import FakeGenerator
from arango_memory.ingest.entities import write_entities
from arango_memory.ingest.extract import (
    ExtractedEntity,
    ExtractedRelation,
    GlinerExtractor,
    HaikuExtractor,
    LayeredExtractor,
    coerce_relation,
)
from arango_memory.ingest.store import store
from arango_memory.ingest.temporal import parse_explicit_time


# ── coerce_relation ───────────────────────────────────────
def test_coerce_relation_maps_to_enum() -> None:
    assert coerce_relation("Caused By") == "caused_by"
    assert coerce_relation("subtopic-of") == "subtopic_of"
    assert coerce_relation("works_with") == "associated_with"  # unknown → generic


# ── GlinerExtractor (injected fakes) ──────────────────────
class _FakeNER:
    def predict_entities(
        self, text: str, labels: Sequence[str], threshold: float = 0.5
    ) -> list[dict[str, object]]:
        return [
            {"text": "Alice", "label": "Person", "score": 0.99},
            {"text": "Acme", "label": "Organization", "score": 0.95},
        ]


def _fake_relations(
    text: str, names: list[str], labels: list[str]
) -> list[tuple[str, str, str]]:
    return [("Alice", "Acme", "caused_by")]


def test_gliner_extractor_entities_and_typed_relations() -> None:
    ext = GlinerExtractor(ner=_FakeNER(), relation_fn=_fake_relations)
    ents = ext.extract("Alice founded Acme")
    assert {(e.name, e.label) for e in ents} == {("Alice", "Person"), ("Acme", "Organization")}
    rels = ext.extract_relations("Alice founded Acme", ents)
    assert rels == [ExtractedRelation("Alice", "Acme", "caused_by")]


# ── HaikuExtractor (FakeGenerator) ────────────────────────
def test_haiku_extractor_parses_json_and_caches_one_call() -> None:
    calls = {"n": 0}

    def handler(prompt: str, system: str | None) -> str:
        calls["n"] += 1
        return (
            '```json\n{"entities": [{"name": "Paris", "label": "Location"}],'
            ' "relations": [{"source": "Paris", "target": "France",'
            ' "relationship": "subtopic_of"}]}\n```'
        )

    ext = HaikuExtractor(generator=FakeGenerator(handler))
    ents = ext.extract("Paris is in France")
    rels = ext.extract_relations("Paris is in France", ents)
    assert [e.name for e in ents] == ["Paris"]
    assert rels == [ExtractedRelation("Paris", "France", "subtopic_of")]
    assert calls["n"] == 1  # one LLM call serves both (cached, §16)


def test_haiku_extractor_empty_on_blank_generation() -> None:
    ext = HaikuExtractor(generator=FakeGenerator())  # default returns ""
    assert ext.extract("anything") == []
    assert ext.extract_relations("anything", []) == []


# ── LayeredExtractor escalation ───────────────────────────
class _Spy:
    def __init__(self, entities: list[ExtractedEntity]) -> None:
        self.entities = entities
        self.calls = 0

    def extract(self, text: str) -> list[ExtractedEntity]:
        self.calls += 1
        return self.entities

    def extract_relations(
        self, text: str, entities: Sequence[ExtractedEntity]
    ) -> list[ExtractedRelation]:
        return []


def test_layered_skips_haiku_when_cheap_tiers_suffice() -> None:
    base = _Spy([ExtractedEntity("Bob", "Person")])
    haiku = _Spy([ExtractedEntity("Escalated", "Concept")])
    layered = LayeredExtractor(base=base, gliner=None, haiku=haiku, escalate_below=1)
    ents = layered.extract("Bob waved")
    assert [e.name for e in ents] == ["Bob"]
    assert haiku.calls == 0  # not escalated


def test_layered_escalates_to_haiku_when_empty() -> None:
    base = _Spy([])
    haiku = _Spy([ExtractedEntity("Escalated", "Concept")])
    layered = LayeredExtractor(base=base, gliner=None, haiku=haiku, escalate_below=1)
    ents = layered.extract("opaque text")
    assert [e.name for e in ents] == ["Escalated"]
    assert haiku.calls == 1


# ── temporal parsing ──────────────────────────────────────
def test_parse_explicit_time_formats() -> None:
    assert parse_explicit_time("We met in 2019.").startswith("2019-01-01")  # type: ignore[union-attr]
    assert parse_explicit_time("Signed on 2021-03-04.").startswith("2021-03-04")  # type: ignore[union-attr]
    assert parse_explicit_time("Born in March 1990").startswith("1990-03-01")  # type: ignore[union-attr]
    assert parse_explicit_time("Due January 5, 2020").startswith("2020-01-05")  # type: ignore[union-attr]
    assert parse_explicit_time("no date here") is None


# ── write_entities: typed relations + valid_time ──────────
class _StubExtractor:
    name = "stub"

    def __init__(
        self, entities: list[ExtractedEntity], relations: list[ExtractedRelation]
    ) -> None:
        self._entities = entities
        self._relations = relations

    def extract(self, text: str) -> list[ExtractedEntity]:
        return self._entities

    def extract_relations(
        self, text: str, entities: Sequence[ExtractedEntity]
    ) -> list[ExtractedRelation]:
        return self._relations


def test_write_entities_writes_typed_relation(db: StandardDatabase) -> None:
    stub = _StubExtractor(
        [ExtractedEntity("Alice", "Person"), ExtractedEntity("Acme", "Organization")],
        [ExtractedRelation("Alice", "Acme", "caused_by")],
    )
    write_entities(
        db, memory_key="m1", episode_key="e1", content="Alice founded Acme in 2019",
        tenant_id="ex1", agent_id="a", extractor=stub, embedder=get_embedder(),
    )
    labels = {r["relationship"] for r in db.aql.execute("FOR r IN relates_to RETURN r")}
    assert labels == {"caused_by"}  # typed relation, not the co-occurrence fallback


def test_write_entities_cooccurrence_fallback(db: StandardDatabase) -> None:
    stub = _StubExtractor(
        [ExtractedEntity("Alice", "Person"), ExtractedEntity("Acme", "Organization")],
        [],  # no typed relations
    )
    write_entities(
        db, memory_key="m1", episode_key="e1", content="Alice and Acme",
        tenant_id="ex2", agent_id="a", extractor=stub, embedder=get_embedder(),
    )
    labels = {r["relationship"] for r in db.aql.execute("FOR r IN relates_to RETURN r")}
    assert labels == {"associated_with"}


def test_explicit_valid_time_on_entities(db: StandardDatabase) -> None:
    store(db, content="We moved to Berlin in 2019", tenant_id="ex3", agent_id="a")
    berlin = next(
        db.aql.execute(
            "FOR e IN entities FILTER e.tenant_id == 'ex3' AND e.name == 'Berlin' RETURN e"
        )
    )
    assert berlin["valid_time_explicit"] is True
    assert berlin["valid_time"].startswith("2019-01-01")
