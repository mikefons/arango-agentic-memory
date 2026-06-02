"""Integration tests against a real ArangoDB Enterprise container (DESIGN.md §22)."""

from __future__ import annotations

from collections.abc import Callable

from arango.database import StandardDatabase

from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult
from arango_memory.schema.collections import DOCUMENT_COLLECTIONS, SEARCH_VIEW, ensure_schema


def test_ensure_schema_is_idempotent(db: StandardDatabase) -> None:
    # `db` fixture already ran ensure_schema once; a second run must not raise.
    ensure_schema(db)

    for name in DOCUMENT_COLLECTIONS:
        assert db.has_collection(name)
    assert SEARCH_VIEW in {v["name"] for v in db.views()}

    # Idempotency index present on episodes + memories, exactly once.
    for name in ("episodes", "memories"):
        idx = [i for i in db.collection(name).indexes() if i.get("name") == "idx_idempotency"]
        assert len(idx) == 1
        assert idx[0]["unique"] is True


def test_store_then_retrieve_round_trip(
    db: StandardDatabase,
    ctx: dict[str, str],
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    result = store(db, content="the quick brown fox jumps", **ctx)
    assert result.episode_id
    assert len(result.memory_ids) == 1

    hits = wait_for_searchable(db, query="quick brown fox", **ctx)
    assert hits.hits, "stored memory was not retrievable via BM25"
    assert "quick brown fox" in hits.context
    assert hits.tokens_injected > 0


def test_store_is_idempotent_on_identical_turn(db: StandardDatabase, ctx: dict[str, str]) -> None:
    # Four identical calls (same tenant/agent/session/content/turn) → one episode + one memory.
    for _ in range(4):
        store(db, content="repeated turn content", **ctx)

    assert db.collection("episodes").count() == 1
    assert db.collection("memories").count() == 1


def test_distinct_turns_create_distinct_records(db: StandardDatabase, ctx: dict[str, str]) -> None:
    store(db, content="first turn", turn_index=0, **ctx)
    store(db, content="second turn", turn_index=1, **ctx)

    assert db.collection("episodes").count() == 2
    assert db.collection("memories").count() == 2


def test_tenant_isolation(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    store(db, content="tenant A secret roster", tenant_id="tenant_a", agent_id="agent_1")
    store(db, content="tenant B secret roster", tenant_id="tenant_b", agent_id="agent_1")

    a_hits = wait_for_searchable(
        db, query="secret roster", tenant_id="tenant_a", agent_id="agent_1"
    )
    assert a_hits.hits
    assert all("tenant A" in h.text for h in a_hits.hits)
    assert not any("tenant B" in h.text for h in a_hits.hits)
