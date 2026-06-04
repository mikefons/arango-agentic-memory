"""Integration tests for entity/edge writes + conflict detection (DESIGN.md §5, §8)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from arango.database import StandardDatabase

from arango_memory.ingest.store import store


class StubEmbedder:
    """Maps known strings to fixed vectors so cosine similarity is controllable."""

    model = "stub"
    version = "1"
    dimensions = 3

    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table

    def embed(self, text: str) -> list[float]:
        return self._table.get(text, [0.0, 0.0, 1.0])

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


def _entities(db: StandardDatabase, tenant: str) -> list[dict[str, Any]]:
    cursor = db.aql.execute(
        "FOR e IN entities FILTER e.tenant_id == @t RETURN e", bind_vars={"t": tenant}
    )
    return list(cursor)


def test_store_populates_entities_and_edges(db: StandardDatabase) -> None:
    result = store(db, content="Alice met Bob in Seattle", tenant_id="t_ent", agent_id="a")
    assert len(result.entity_ids) == 3  # Alice, Bob, Seattle

    assert db.collection("entities").count() == 3
    assert db.collection("mentions").count() == 3      # memory → each entity
    assert db.collection("produced_by").count() == 3   # each entity → episode
    assert db.collection("relates_to").count() == 3    # C(3,2) co-occurrence pairs


def test_mention_count_increments_across_turns(db: StandardDatabase) -> None:
    store(db, content="Acme launched", tenant_id="t_m", agent_id="a", turn_index=0)
    store(db, content="Acme expanded", tenant_id="t_m", agent_id="a", turn_index=1)

    rows = _entities(db, "t_m")
    assert len(rows) == 1
    assert rows[0]["name"] == "Acme"
    assert rows[0]["mention_count"] == 2


def test_idempotent_replay_does_not_double_count(db: StandardDatabase) -> None:
    for _ in range(3):
        store(db, content="Globex rises", tenant_id="t_idem", agent_id="a", turn_index=0)

    rows = _entities(db, "t_idem")
    assert len(rows) == 1
    assert rows[0]["mention_count"] == 1               # replays skip extraction
    assert db.collection("mentions").count() == 1


def test_entities_are_tenant_scoped(db: StandardDatabase) -> None:
    store(db, content="Wayne Industries", tenant_id="t_a", agent_id="a")
    store(db, content="Wayne Industries", tenant_id="t_b", agent_id="a")
    assert db.collection("entities").count() == 2      # one per tenant


def test_conflict_merge_above_threshold(db: StandardDatabase) -> None:
    emb = StubEmbedder({"Alice": [1.0, 0.0, 0.0], "Alicia": [0.97, 0.24, 0.0]})
    store(db, content="Alice", tenant_id="t_c", agent_id="a", turn_index=0, embedder=emb)
    store(db, content="Alicia", tenant_id="t_c", agent_id="a", turn_index=1, embedder=emb)

    rows = _entities(db, "t_c")
    assert [r["name"] for r in rows] == ["Alice"]      # Alicia merged into Alice
    assert rows[0]["mention_count"] == 2


def test_conflict_flag_in_review_band(db: StandardDatabase) -> None:
    emb = StubEmbedder({"Alice": [1.0, 0.0, 0.0], "Alfred": [0.7, 0.714, 0.0]})  # cos ≈ 0.70
    store(db, content="Alice", tenant_id="t_f", agent_id="a", turn_index=0, embedder=emb)
    store(db, content="Alfred", tenant_id="t_f", agent_id="a", turn_index=1, embedder=emb)

    rows = {r["name"]: r for r in _entities(db, "t_f")}
    assert set(rows) == {"Alice", "Alfred"}
    assert rows["Alfred"]["needs_review"] is True
    assert rows["Alfred"]["conflict_with"] is not None


def test_conflict_distinct_creates_new(db: StandardDatabase) -> None:
    emb = StubEmbedder({"Alice": [1.0, 0.0, 0.0], "Zoe": [0.0, 0.0, 1.0]})  # cos = 0
    store(db, content="Alice", tenant_id="t_n", agent_id="a", turn_index=0, embedder=emb)
    store(db, content="Zoe", tenant_id="t_n", agent_id="a", turn_index=1, embedder=emb)

    rows = {r["name"]: r for r in _entities(db, "t_n")}
    assert set(rows) == {"Alice", "Zoe"}
    assert rows["Zoe"]["needs_review"] is False
