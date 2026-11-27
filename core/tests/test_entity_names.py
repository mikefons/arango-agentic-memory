"""One name is one entity, whatever type the extractor gave it or legal suffix it carries."""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

import pytest
from arango.database import StandardDatabase

from arango_memory.client import ArangoMemoryClient
from arango_memory.ingest import entities
from arango_memory.ingest.entities import name_key
from arango_memory.ingest.extract import ExtractedEntity, ExtractedRelation
from arango_memory.ingest.store import StoreItem, store, store_many
from arango_memory.security.forget import purge


class _Listed:
    """Extracts "name:Label" pieces separated by ";" — typed output without a model."""

    name = "listed"
    io_bound = False

    def extract(self, text: str) -> list[ExtractedEntity]:
        return [ExtractedEntity(*piece.split(":")) for piece in text.split(";")]

    def extract_relations(
        self, text: str, ents: Sequence[ExtractedEntity]
    ) -> list[ExtractedRelation]:
        return []


def _write(db: StandardDatabase, *texts: str, batched: bool, tenant: str = "nm",
           start: int = 0) -> None:
    if batched:
        store_many(db, [StoreItem(content=t, turn_index=start + i) for i, t in enumerate(texts)],
                   tenant_id=tenant, agent_id="a", extract=True, extractor=_Listed())
    else:
        for i, text in enumerate(texts):
            store(db, content=text, tenant_id=tenant, agent_id="a", turn_index=start + i,
                  extractor=_Listed())


def _entities(db: StandardDatabase, tenant: str = "nm") -> list[tuple[str, str, int]]:
    rows = db.aql.execute(
        "FOR e IN entities FILTER e.tenant_id == @t RETURN [e.name, e.label, e.mention_count]",
        bind_vars={"t": tenant})
    return sorted(tuple(r) for r in rows)  # type: ignore[union-attr,arg-type]


def _count(db: StandardDatabase, query: str) -> int:
    return len(list(db.aql.execute(query)))  # type: ignore[arg-type]


@pytest.mark.parametrize("name,key", [
    ("Acme", "acme"), ("  ACME  ", "acme"), ("Acme Inc.", "acme"), ("Acme, LLC", "acme"),
    ("Brayton & Kell LLP", "brayton & kell"), ("Acme Corporation", "acme"),
    ("Incorporated", "incorporated"), ("Zinc", "zinc"), ("Acme Inc Holdings", "acme inc holdings"),
    ("Acme Robotics", "acme robotics"), ("The Hill", "the hill"),
])
def test_name_key(name: str, key: str) -> None:
    assert name_key(name) == key


@pytest.mark.parametrize("batched", [True, False], ids=["batched", "single"])
def test_type_and_suffix_do_not_split_a_name(db: StandardDatabase, batched: bool) -> None:
    _write(db, "Acme:Organization;Zed:Person", "Acme Inc.:Person;ACME:Concept",
           "Acme:Location", batched=batched)
    # three turns mention Acme: one entity, counted once per turn, keeping its first type
    assert _entities(db) == [("Acme", "Organization", 3), ("Zed", "Person", 1)]
    assert _count(db, "FOR m IN mentions RETURN 1") == 4
    # Acme's two spellings in one turn never relate it to itself
    assert _count(db, "FOR r IN relates_to FILTER r._from == r._to RETURN 1") == 0
    assert _count(db, "FOR r IN relates_to RETURN 1") == 1


@pytest.mark.parametrize("batched", [True, False], ids=["batched", "single"])
def test_a_later_write_joins_the_existing_name(db: StandardDatabase, batched: bool) -> None:
    _write(db, "Acme LLP:Organization", batched=batched)
    _write(db, "acme:Person", batched=batched, start=1)
    assert _entities(db) == [("Acme LLP", "Organization", 2)]


def test_a_typed_form_names_a_new_entity(db: StandardDatabase) -> None:
    _write(db, "acme:Concept", "Acme:Organization", batched=True)
    assert _entities(db) == [("Acme", "Organization", 2)]


@pytest.mark.parametrize("batched", [True, False], ids=["batched", "single"])
def test_entities_stored_before_this_rule_are_found_by_name(
    db: StandardDatabase, batched: bool
) -> None:
    _write(db, "Acme:Organization", batched=batched)
    old = dict(next(iter(db.aql.execute("FOR e IN entities RETURN e"))))  # type: ignore[arg-type]
    db.collection("entities").delete(old["_key"])
    db.collection("entities").insert({**old, "_key": "legacy-random-key"})
    _write(db, "Acme:Organization", "ACME Ltd:Person", batched=batched, start=1)
    assert _entities(db) == [("Acme", "Organization", 3)]


def test_a_deprecated_entity_does_not_absorb_another_type(db: StandardDatabase) -> None:
    rows = [{"key": "dead", "label": "Person", "invalid": True}]
    assert entities._name_match(rows, "Organization") is None
    assert entities._name_match(rows, "Person") == "dead"
    live = {"key": "live", "label": "Location", "invalid": False}
    assert entities._name_match([*rows, live], "Organization") == "live"
    assert entities._name_match([*rows, live], "Person") == "dead"  # its own label first


def test_names_stay_apart_across_tenants_and_return_after_purge(db: StandardDatabase) -> None:
    _write(db, "Acme:Organization", batched=True, tenant="one")
    _write(db, "Acme:Organization", batched=True, tenant="two")
    assert _entities(db, "one") == _entities(db, "two") == [("Acme", "Organization", 1)]
    purge(db, tenant_id="one")
    _write(db, "Acme:Organization", batched=True, tenant="one", start=1)
    assert _entities(db, "one") == [("Acme", "Organization", 1)]


def test_parallel_writers_of_one_new_name_make_one_entity(
    db: StandardDatabase, client: ArangoMemoryClient
) -> None:
    forms = ["Acme:Organization", "Acme Inc.:Person", "ACME:Concept", "Acme LLC:Location"]

    def write(n: int) -> None:
        handle = ArangoMemoryClient(client.config).connect()
        for round_ in range(8):
            _write(handle, forms[(n + round_) % 4], batched=True, start=n * 100 + round_)

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(write, range(4)))
    assert [(r[2]) for r in _entities(db)] == [32]
