"""IN-2 batched graph pass: `write_entities_many` (via store_many extract=True) must build the
SAME graph as the per-item `store(extract=True)` path — belief/corroboration fold by sum."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

import pytest
from arango.database import StandardDatabase

from arango_memory.embedding import FakeEmbedder
from arango_memory.ingest.entities import GraphMemory, write_entities_many
from arango_memory.ingest.extract import (
    ExtractedEntity,
    ExtractedRelation,
    FakeExtractor,
    HaikuExtractor,
    LayeredExtractor,
    is_io_bound,
)
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


class _ThreadRecorder:
    """Records which thread ran each `extract`; returns no entities, so `write_entities_many`
    stops after step 1 and never touches the DB."""

    def __init__(self, **attrs: Any) -> None:
        self.name = "recorder"
        self.__dict__.update(attrs)
        self.idents: list[int] = []

    def extract(self, text: str) -> list[ExtractedEntity]:
        self.idents.append(threading.get_ident())
        return []

    def extract_relations(
        self, text: str, entities: Sequence[ExtractedEntity]
    ) -> list[ExtractedRelation]:
        return []


@pytest.mark.parametrize(
    ("attrs", "threaded"),
    [
        ({"io_bound": True}, True),
        ({"io_bound": False}, False),
        ({}, True),  # no attribute (third-party, pre-flag) → I/O-bound, today's behavior
    ],
)
def test_extraction_threads_only_io_bound_extractors(
    attrs: dict[str, Any], threaded: bool
) -> None:
    # IN-7 concurrency helps an LLM extractor but GIL-convoys a CPU-bound one (spaCy: 3x slower
    # at 8 threads), so only I/O-bound extractors take the pool; the rest run on the caller.
    rec = _ThreadRecorder(**attrs)
    mems = [GraphMemory(memory_key=f"m{i}", episode_key=f"e{i}", content=t)
            for i, t in enumerate(_TURNS)]
    out = write_entities_many(
        None,  # type: ignore[arg-type]  # never reached: no entities extracted
        mems, tenant_id="t", agent_id="a", extractor=rec, embedder=FakeEmbedder(),
    )
    assert out == {m.memory_key: [] for m in mems}
    assert len(rec.idents) == len(_TURNS)
    caller = threading.get_ident()
    if threaded:
        assert caller not in rec.idents  # pool workers, never the calling thread
    else:
        assert set(rec.idents) == {caller}


def test_io_bound_declarations() -> None:
    assert not is_io_bound(FakeExtractor())
    assert is_io_bound(HaikuExtractor())  # generator loads lazily; no key needed
    assert is_io_bound(_ThreadRecorder())  # missing attribute → I/O-bound
    cheap = LayeredExtractor(base=FakeExtractor())
    assert not is_io_bound(cheap)  # no LLM tier to escalate to
    assert is_io_bound(LayeredExtractor(base=FakeExtractor(), haiku=HaikuExtractor()))


def _seed_entities(db: StandardDatabase, tenant: str, n: int, *, dim: int, seed: int) -> None:
    import random

    rnd = random.Random(seed)
    db.collection("entities").insert_many([
        {"tenant_id": tenant, "name": f"{tenant}_{i}", "label": "X",
         "embedding": [rnd.gauss(0.0, 1.0) for _ in range(dim)]}
        for i in range(n)
    ], silent=True)


def test_ann_resolution_pool_is_per_query_vector(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The batched ANN pool must hold each query vector's own top-k. The old query put SORT/LIMIT
    # at the top level of the nested FOR, so ArangoDB returned top-k rows for the WHOLE batch
    # (2 here) and most entities resolved against the wrong candidates.
    from arango_memory.config import settings as s
    from arango_memory.ingest import entities as ent

    monkeypatch.setattr(s, "entity_vector_n_lists", 2)
    monkeypatch.setattr(s, "entity_vector_train_factor", 1)
    monkeypatch.setattr(s, "entity_resolution_scan_max", 0)
    monkeypatch.setattr(s, "entity_resolution_top_k", 2)
    monkeypatch.setattr(s, "n_probe", 2)  # search every IVF cell → exact on this tiny index
    monkeypatch.setattr(ent, "_ANN_QVEC_CHUNK", 7)  # exercise chunking
    _seed_entities(db, "big", 30, dim=8, seed=1)
    _seed_entities(db, "other", 30, dim=8, seed=2)
    rows = list(db.aql.execute(
        "FOR e IN entities FILTER e.tenant_id == 'big' RETURN {name: e.name, v: e.embedding}"
    ))

    assert ent._use_ann(db, "big", dimensions=8) is True
    pool = ent._resolution_pool(db, "big", [r["v"] for r in rows], use_ann=True)
    assert {p["name"] for p in pool} == {r["name"] for r in rows}  # every vector finds itself
    assert all(p["embedding"] for p in pool)  # rows fetched in full for the numpy match


def test_resolution_cost_is_independent_of_other_tenants(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A small tenant resolves by an exact tenant-scoped scan even once the shared index is warm:
    # filtered ANN widens until it finds top-k rows of the tenant, so its cost grew with OTHER
    # tenants' entities (OOM-killed a many-tenant LongMemEval database). Same queries, same rows
    # read, whatever the rest of the collection holds.
    from arango.aql import AQL

    from arango_memory.config import settings as s
    from arango_memory.ingest import entities as ent

    monkeypatch.setattr(s, "entity_vector_n_lists", 2)
    monkeypatch.setattr(s, "entity_vector_train_factor", 1)
    _seed_entities(db, "small", 5, dim=8, seed=1)
    _seed_entities(db, "noisy", 20, dim=8, seed=2)
    qvecs = [[1.0] * 8, [-1.0] * 8]

    original = AQL.execute

    def resolve() -> tuple[list[str], int, int]:
        seen: list[tuple[str, Any]] = []

        def recording(self: AQL, query: str, *args: object, **kwargs: object) -> object:
            cur = original(self, query, *args, **kwargs)
            seen.append((query, cur))
            return cur

        monkeypatch.setattr(AQL, "execute", recording)
        try:
            pool = ent._resolution_pool(
                db, "small", qvecs, use_ann=ent._use_ann(db, "small", dimensions=8)
            )
        finally:
            monkeypatch.setattr(AQL, "execute", original)
        read = sum(c.statistics()["scanned_index"] + c.statistics()["scanned_full"]
                   for _, c in seen)
        return [q for q, _ in seen], read, len(pool)

    small = resolve()
    _seed_entities(db, "noisy", 500, dim=8, seed=3)
    large = resolve()

    assert small == large
    assert not any("APPROX_NEAR" in q for q in large[0])
    assert large[2] == 5
