"""IN-2 batched graph pass: `write_entities_many` (via store_many extract=True) must build the
SAME graph as the per-item `store(extract=True)` path — belief/corroboration fold by sum."""

from __future__ import annotations

from collections.abc import Sequence

from arango.database import StandardDatabase

from arango_memory.ingest.extract import ExtractedEntity, ExtractedRelation, FakeExtractor
from arango_memory.ingest.store import StoreItem, store, store_many

# Capitalized spans → entities (FakeExtractor). Acme appears in two memories (accumulation);
# the rest are distinct so FakeEmbedder produces no spurious semantic merges.
_TURNS = [
    "Alice met Bob at Acme.",
    "Carol joined Acme.",
    "Dave left.",
]


def _entities(db: StandardDatabase, tenant: str) -> dict[str, tuple[int, float]]:
    cur = db.aql.execute(
        "FOR e IN entities FILTER e.tenant_id == @t "
        "RETURN {name: e.name, mc: e.mention_count, belief: e.belief}",
        bind_vars={"t": tenant},
    )
    return {r["name"]: (r["mc"], round(r["belief"], 9)) for r in cur}


def _relates(db: StandardDatabase, tenant: str) -> list[tuple[str, int, float]]:
    cur = db.aql.execute(
        "FOR r IN relates_to LET e = DOCUMENT(r._from) FILTER e.tenant_id == @t "
        "RETURN {rel: r.relationship, corr: r.corroboration, belief: r.belief}",
        bind_vars={"t": tenant},
    )
    return sorted((r["rel"], r["corr"], round(r["belief"], 9)) for r in cur)


def _edge_count(db: StandardDatabase, collection: str, tenant: str, side: str) -> int:
    cur = db.aql.execute(
        f"FOR x IN {collection} LET e = DOCUMENT(x.{side}) FILTER e.tenant_id == @t "
        "COLLECT WITH COUNT INTO c RETURN c",
        bind_vars={"t": tenant},
    )
    return int(next(iter(cur), 0))


def test_batched_graph_equals_per_item(db: StandardDatabase) -> None:
    # per-item path
    for i, turn in enumerate(_TURNS):
        store(db, content=turn, tenant_id="seq", agent_id="a", turn_index=i, extract=True)
    # batched path
    results = store_many(
        db,
        [StoreItem(content=t, turn_index=i) for i, t in enumerate(_TURNS)],
        tenant_id="bat", agent_id="a", extract=True,
    )

    # entities: identical names, mention_counts, and beliefs (belief is a function of Σrel).
    assert _entities(db, "bat") == _entities(db, "seq")
    # Acme is mentioned by two memories → accumulated mention_count of 2.
    assert _entities(db, "bat")["Acme"][0] == 2

    # relates_to edges: identical set of (relationship, corroboration, belief).
    assert _relates(db, "bat") == _relates(db, "seq")

    # mention + produced_by edge counts match the per-item path.
    assert _edge_count(db, "mentions", "bat", "_to") == _edge_count(db, "mentions", "seq", "_to")
    assert _edge_count(db, "produced_by", "bat", "_from") == _edge_count(
        db, "produced_by", "seq", "_from"
    )

    # store_many(extract=True) surfaces the resolved entity keys per memory.
    entity_ids = [k for r in results for k in r.entity_ids]
    assert entity_ids  # graph was built
    assert all(db.collection("entities").get(k) is not None for k in entity_ids)


def test_batched_graph_is_retrievable(db: StandardDatabase) -> None:
    from arango_memory.retrieve.search import force_view_sync, retrieve

    store_many(
        db,
        [StoreItem(content=t, turn_index=i) for i, t in enumerate(_TURNS)],
        tenant_id="ret", agent_id="a", extract=True,
    )
    force_view_sync(db, "ret")
    hits = retrieve(db, query="Acme", tenant_id="ret", agent_id="a").hits
    assert hits  # bulk-recorded + graph-reflected memories retrieve


def test_store_many_extract_false_builds_no_graph(db: StandardDatabase) -> None:
    store_many(
        db,
        [StoreItem(content=t, turn_index=i) for i, t in enumerate(_TURNS)],
        tenant_id="norec", agent_id="a", extract=False,
    )
    assert _entities(db, "norec") == {}  # record-only path mints no entities


class _FlakyExtractor:
    """FakeExtractor that raises on one target content — simulates a transient LLM 5xx mid-batch."""

    def __init__(self, fail_on: str) -> None:
        self.name = "flaky"
        self._fake = FakeExtractor()
        self._fail_on = fail_on

    def extract(self, text: str) -> list[ExtractedEntity]:
        if self._fail_on in text:
            raise RuntimeError("simulated transient extraction failure")
        return self._fake.extract(text)

    def extract_relations(
        self, text: str, entities: Sequence[ExtractedEntity]
    ) -> list[ExtractedRelation]:
        return self._fake.extract_relations(text, entities)


def test_extraction_is_fail_soft(db: StandardDatabase) -> None:
    # One memory's extraction failing must not crash the whole batch (a transient 5xx over a long
    # graph-on run): the other memories' entities still land, the failed one just contributes none.
    store_many(
        db,
        [StoreItem(content=t, turn_index=i) for i, t in enumerate(_TURNS)],
        tenant_id="failsoft", agent_id="a", extract=True,
        extractor=_FlakyExtractor(fail_on="Carol"),  # _TURNS[1] "Carol joined Acme."
    )
    ents = _entities(db, "failsoft")
    assert {"Alice", "Bob", "Dave"} <= set(ents)  # good memories extracted, batch survived
    assert "Carol" not in ents                    # the failed memory contributed no entities


def test_store_many_replay_does_not_double_count(db: StandardDatabase) -> None:
    # Replaying the same batch (same idempotency keys) must leave the graph untouched (§8):
    # the UPSERTs add to mention_count / reliability_sum / corroboration, so a replay that
    # re-fed the graph pass would double every count.
    items = [StoreItem(content=t, turn_index=i) for i, t in enumerate(_TURNS)]
    first = store_many(db, items, tenant_id="replay", agent_id="a", extract=True)
    ents, rels = _entities(db, "replay"), _relates(db, "replay")
    assert ents["Acme"][0] == 2 and rels

    second = store_many(db, items, tenant_id="replay", agent_id="a", extract=True)
    assert _entities(db, "replay") == ents
    assert _relates(db, "replay") == rels
    # Record is unchanged; replayed items report no newly-written entities (like store()).
    assert [r.memory_ids for r in second] == [r.memory_ids for r in first]
    assert all(r.entity_ids == [] for r in second)
