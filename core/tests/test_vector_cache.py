"""PF-1h: the batched write keeps entity embeddings, and a scan returns only the others."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from arango.database import StandardDatabase

from arango_memory.config import settings
from arango_memory.embedding import FakeEmbedder
from arango_memory.ingest import entities
from arango_memory.ingest.store import StoreItem, store_many
from arango_memory.ingest.vector_cache import EntityVectorCache, entity_vectors
from arango_memory.security.forget import purge


@pytest.fixture(autouse=True)
def _empty_cache() -> None:
    entity_vectors.clear()


def _write(db: StandardDatabase, text: str, turn: int, tenant: str = "vc") -> None:
    store_many(db, [StoreItem(content=text, turn_index=turn)], tenant_id=tenant, agent_id="a",
               extract=True)


def _downloaded(db: StandardDatabase, tenant: str = "vc") -> dict[str, bool]:
    """name -> did this scan have to send its embedding?"""
    return {r["name"]: not r["cached"] for r in entities._scan_pool(db, tenant)}


def test_a_scan_downloads_only_what_is_new(db: StandardDatabase) -> None:
    _write(db, "Alice met Bob.", 0)
    assert _downloaded(db) == {"Alice": True, "Bob": True}  # first sight
    assert _downloaded(db) == {"Alice": False, "Bob": False}
    _write(db, "Carol left.", 1)
    assert _downloaded(db) == {"Alice": False, "Bob": False, "Carol": True}


def test_cached_rows_match_the_uncached_scan(db: StandardDatabase) -> None:
    _write(db, "Alice met Bob at Acme.", 0)
    entities._scan_pool(db, "vc")
    cached = {r["key"]: r for r in entities._scan_pool(db, "vc")}
    plain = list(db.aql.execute(entities._FETCH_EXISTING, bind_vars={"tenant_id": "vc"}))  # type: ignore[arg-type]
    assert len(cached) == len(plain) == 3
    for row in plain:
        mine = cached[row["key"]]
        assert (mine["name"], mine["label"]) == (row["name"], row["label"])
        assert np.array_equal(mine["embedding"], row["embedding"])  # exact, not approximate


def test_semantic_merge_uses_a_cached_vector(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _SameForAcme(FakeEmbedder):
        def embed(self, text: str) -> list[float]:
            return super().embed("Acme" if text == "Acmeco" else text)

    emb = _SameForAcme()
    monkeypatch.setattr(settings, "embedding_cache", False)
    for turn, text in enumerate(["Acme opened.", "Zed arrived.", "Acmeco grew."]):
        store_many(db, [StoreItem(content=text, turn_index=turn)], tenant_id="vc", agent_id="a",
                   extract=True, embedder=emb)
    rows = dict(db.aql.execute(  # type: ignore[arg-type]
        "FOR e IN entities FILTER e.tenant_id == 'vc' RETURN [e.name, e.mention_count]"))
    assert rows["Acme"] == 2 and "Acmeco" not in rows


def test_a_reembedded_entity_is_downloaded_again(db: StandardDatabase) -> None:
    _write(db, "Alice met Bob.", 0)
    _downloaded(db)
    fresh = [1.0] + [0.0] * (settings.embedding_dimensions - 1)
    db.aql.execute(
        "FOR e IN entities FILTER e.name == 'Bob' UPDATE e WITH "
        "{ embedding: @v, embedding_version: 'next', reembedded_at: '2026-01-01' } IN entities",
        bind_vars={"v": fresh},
    )
    rows = {r["name"]: r for r in entities._scan_pool(db, "vc")}
    assert not rows["Bob"]["cached"] and rows["Alice"]["cached"]
    assert np.array_equal(rows["Bob"]["embedding"], fresh)


def test_deleted_and_purged_entities_leave_the_cache(db: StandardDatabase) -> None:
    _write(db, "Alice met Bob.", 0)
    _downloaded(db)
    db.aql.execute("FOR e IN entities FILTER e.name == 'Bob' REMOVE e IN entities")
    assert set(_downloaded(db)) == {"Alice"}
    assert set(entity_vectors.get((db.name, "vc"))) == {
        r["key"] for r in entities._scan_pool(db, "vc")}
    purge(db, tenant_id="vc")
    assert entity_vectors.get((db.name, "vc")) == {} and entity_vectors.size_bytes == 0


def test_tenants_do_not_share_vectors(db: StandardDatabase) -> None:
    _write(db, "Alice met Bob.", 0, tenant="one")
    _write(db, "Alice met Bob.", 0, tenant="two")
    assert all(_downloaded(db, "one").values())
    assert all(_downloaded(db, "two").values())


def test_zero_budget_turns_the_cache_off(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "entity_vector_cache_mb", 0)
    _write(db, "Alice met Bob.", 0)
    rows = entities._scan_pool(db, "vc")
    assert all("cached" not in r and r["embedding"] for r in rows)
    assert entity_vectors.size_bytes == 0


def _entry(*keys: str) -> dict[str, Any]:
    return {k: ("s", np.zeros(100)) for k in keys}  # 800 bytes + overhead = 1000 each


def test_budget_keeps_a_stable_part_and_evicts_the_oldest_tenant() -> None:
    cache = EntityVectorCache()
    cache.put(("db", "big"), _entry("a", "b", "c"), max_bytes=2500)
    assert list(cache.get(("db", "big"))) == ["a", "b"] and cache.size_bytes == 2000
    cache.put(("db", "x"), _entry("x"), max_bytes=2500)
    assert cache.get(("db", "big")) == {} and cache.size_bytes == 1000
    cache.put(("db", "y"), _entry("y"), max_bytes=2500)
    cache.get(("db", "x"))  # now the most recently used
    cache.put(("db", "z"), _entry("z"), max_bytes=2500)
    assert cache.get(("db", "y")) == {} and cache.get(("db", "x"))
