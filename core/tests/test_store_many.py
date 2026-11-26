"""IN-1 bulk record path: `store_many` — O(1) round trips, drop-in for store(extract=False)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from arango.database import StandardDatabase

from arango_memory.embedding import FakeEmbedder
from arango_memory.ingest.store import StoreItem, store, store_many
from arango_memory.retrieve.search import force_view_sync, retrieve


# ── round-trip proof (no DB — a recording double) ─────────
class _RecordingAQL:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(self, query: str, *, bind_vars: dict[str, Any]) -> Iterator[list[str]]:
        self.calls.append(bind_vars)
        return iter([[d["_key"] for d in bind_vars["episodes"]]])


class _RecordingDB:
    def __init__(self) -> None:
        self.aql = _RecordingAQL()


def test_store_many_is_o1_round_trips() -> None:
    db = _RecordingDB()
    items = [StoreItem(content=f"turn number {i}", turn_index=i) for i in range(50)]
    results = store_many(db, items, tenant_id="t", agent_id="a", embedder=FakeEmbedder())  # type: ignore[arg-type]
    assert len(results) == 50
    # exactly ONE statement records both collections for all 50 items — not 50 inserts.
    assert [(len(c["episodes"]), len(c["memories"])) for c in db.aql.calls] == [(50, 50)]


def test_store_many_empty_is_noop() -> None:
    assert store_many(_RecordingDB(), [], tenant_id="t", agent_id="a") == []  # type: ignore[arg-type]


# ── drop-in equality + retrievability (DB) ────────────────
def test_store_many_matches_single_store(db: StandardDatabase) -> None:
    # The same logical turn via store(extract=False) and store_many must resolve to the SAME
    # idempotency key + memory doc — so bulk is a drop-in for the per-turn record path.
    r_single = store(
        db, content="Zeta fact", tenant_id="tA", agent_id="a", turn_index=0, extract=False
    )
    r_bulk = store_many(
        db, [StoreItem(content="Zeta fact", turn_index=0)], tenant_id="tA", agent_id="a"
    )[0]
    assert r_bulk.episode_id == r_single.episode_id
    assert r_bulk.memory_ids == r_single.memory_ids

    mem = db.collection("memories").get(r_bulk.memory_ids[0])
    assert mem is not None
    assert mem["text"] == "Zeta fact" and mem["type"] == "episodic" and mem["embedding"]


def test_store_many_is_immediately_retrievable(db: StandardDatabase) -> None:
    items = [StoreItem(content=f"Orbital mechanics fact {i}", turn_index=i) for i in range(4)]
    results = store_many(db, items, tenant_id="tR", agent_id="a")
    assert len(results) == 4
    force_view_sync(db, "tR")  # MA-1: bulk record is read-your-writes consistent
    hits = retrieve(db, query="Orbital mechanics", tenant_id="tR", agent_id="a").hits
    assert hits


def test_store_many_is_idempotent(db: StandardDatabase) -> None:
    items = [StoreItem(content=f"Idem fact {i}", turn_index=i) for i in range(3)]
    store_many(db, items, tenant_id="tI", agent_id="a")
    before = db.collection("memories").count()
    store_many(db, items, tenant_id="tI", agent_id="a")  # replay
    assert db.collection("memories").count() == before  # same keys → no duplicates


def _mention_counts(db: StandardDatabase, tenant: str) -> dict[str, int]:
    rows = db.aql.execute(
        "FOR e IN entities FILTER e.tenant_id == @t RETURN [e.name, e.mention_count]",
        bind_vars={"t": tenant},
    )
    return {name: count for name, count in rows}  # type: ignore[union-attr]


def test_only_new_turns_feed_the_graph_once_each(db: StandardDatabase) -> None:
    old = StoreItem(content="Alice left.", turn_index=0)
    new = StoreItem(content="Alice met Bob.", turn_index=1)
    store_many(db, [old], tenant_id="tG", agent_id="a", extract=True)
    # a replayed turn, a new one, and the new one again in the same batch
    results = store_many(db, [old, new, new], tenant_id="tG", agent_id="a", extract=True)
    assert _mention_counts(db, "tG") == {"Alice": 2, "Bob": 1}
    assert results[0].entity_ids == []
    assert results[1].entity_ids == results[2].entity_ids != []

