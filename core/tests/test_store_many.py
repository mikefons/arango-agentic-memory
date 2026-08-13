"""IN-1 bulk record path: `store_many` — O(1) round trips, drop-in for store(extract=False)."""

from __future__ import annotations

import pytest
from arango.database import StandardDatabase

from arango_memory.embedding import FakeEmbedder
from arango_memory.ingest.store import StoreItem, store, store_many
from arango_memory.retrieve.search import force_view_sync, retrieve


# ── round-trip proof (no DB — a recording double) ─────────
class _RecordingCollection:
    def __init__(self) -> None:
        self.insert_many_sizes: list[int] = []
        self.insert_calls = 0

    def insert_many(self, docs: list[dict], **_: object) -> None:
        self.insert_many_sizes.append(len(docs))

    def insert(self, doc: dict, **_: object) -> None:
        self.insert_calls += 1


class _RecordingDB:
    def __init__(self) -> None:
        self.cols: dict[str, _RecordingCollection] = {}

    def collection(self, name: str) -> _RecordingCollection:
        return self.cols.setdefault(name, _RecordingCollection())


def test_store_many_is_o1_round_trips() -> None:
    db = _RecordingDB()
    items = [StoreItem(content=f"turn number {i}", turn_index=i) for i in range(50)]
    results = store_many(db, items, tenant_id="t", agent_id="a", embedder=FakeEmbedder())  # type: ignore[arg-type]
    assert len(results) == 50
    # exactly ONE bulk insert per collection, for all 50 items — not 50 per-item inserts.
    assert db.cols["episodes"].insert_many_sizes == [50]
    assert db.cols["memories"].insert_many_sizes == [50]
    assert db.cols["episodes"].insert_calls == 0
    assert db.cols["memories"].insert_calls == 0


def test_store_many_extract_true_raises() -> None:
    with pytest.raises(NotImplementedError, match="IN-2"):
        store_many(
            _RecordingDB(), [StoreItem(content="x")],  # type: ignore[arg-type]
            tenant_id="t", agent_id="a", extract=True, embedder=FakeEmbedder(),
        )


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
