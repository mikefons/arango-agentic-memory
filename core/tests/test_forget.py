"""Right-to-be-forgotten (soft-delete + purge) and ABAC enforcement (DESIGN.md §17)."""

from __future__ import annotations

import time
from collections.abc import Callable

from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult, retrieve
from arango_memory.security.forget import forget, forget_memories, purge


def _eventually_empty(db: StandardDatabase, query: str, ctx: dict[str, str]) -> bool:
    """Poll retrieval until the soft-deleted data drops out (view is eventually consistent)."""
    for _ in range(20):
        if not retrieve(db, query=query, **ctx).hits:
            return True
        time.sleep(0.25)
    return False


# ── soft-delete ───────────────────────────────────────────
def test_forget_hides_subject_and_spares_others(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    a = {"tenant_id": "f_a", "agent_id": "x"}
    b = {"tenant_id": "f_b", "agent_id": "x"}
    store(db, content="alpha shared roster", **a)
    store(db, content="beta shared roster", **b)
    wait_for_searchable(db, query="roster", **a)

    counts = forget(db, tenant_id="f_a")
    assert counts["memories"] >= 1

    assert _eventually_empty(db, "roster", a)                    # forgotten
    assert wait_for_searchable(db, query="roster", **b).hits     # other tenant intact


def test_forget_is_agent_scoped(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    a1 = {"tenant_id": "f_s", "agent_id": "a1"}
    a2 = {"tenant_id": "f_s", "agent_id": "a2"}
    store(db, content="agent one data", **a1)
    store(db, content="agent two data", **a2)
    wait_for_searchable(db, query="data", **a1)

    forget(db, tenant_id="f_s", agent_id="a1")
    assert _eventually_empty(db, "data", a1)
    assert wait_for_searchable(db, query="data", **a2).hits


# ── physical purge ────────────────────────────────────────
def test_purge_hard_deletes_subject_and_edges(db: StandardDatabase) -> None:
    store(db, content="Alice met Bob", tenant_id="p_a", agent_id="x")
    store(db, content="Carol kept data", tenant_id="p_b", agent_id="x")
    assert db.collection("entities").count() >= 2
    assert db.collection("mentions").count() >= 1

    counts = purge(db, tenant_id="p_a")
    assert counts["memories"] >= 1
    assert counts["entities"] >= 2
    assert counts["edges"] >= 1

    gone = list(db.aql.execute("FOR e IN entities FILTER e.tenant_id == 'p_a' RETURN e"))
    assert gone == []
    kept = list(db.aql.execute("FOR m IN memories FILTER m.tenant_id == 'p_b' RETURN m"))
    assert kept != []  # other tenant untouched


# ── ABAC ──────────────────────────────────────────────────
def _ctx(level: str) -> dict[str, str]:
    return {"tenant_id": "t_abac", "agent_id": "a", "access_level": level}


def test_abac_store_requires_write(api: TestClient) -> None:
    assert api.post("/v1/store", json={"content": "x", "ctx": _ctx("read")}).status_code == 403
    assert api.post("/v1/store", json={"content": "x", "ctx": _ctx("write")}).status_code == 200


def test_abac_retrieve_allows_read(api: TestClient) -> None:
    resp = api.post("/v1/retrieve", json={"query": "x", "ctx": _ctx("read")})
    assert resp.status_code == 200


def test_forget_endpoint_requires_write(api: TestClient) -> None:
    api.post("/v1/store", json={"content": "alpha roster", "ctx": _ctx("write")})
    denied = api.post("/v1/forget", json={"tenant_id": "t_abac", "access_level": "read"})
    assert denied.status_code == 403
    ok = api.post("/v1/forget", json={"tenant_id": "t_abac", "access_level": "write"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "forgotten"


def test_forget_endpoint_hard_purges(api: TestClient) -> None:
    api.post("/v1/store", json={"content": "gamma roster", "ctx": _ctx("write")})
    ok = api.post(
        "/v1/forget", json={"tenant_id": "t_abac", "access_level": "write", "hard": True}
    )
    assert ok.status_code == 200 and ok.json()["status"] == "purged"


# ── the reset+rerun bug: soft-delete leaves the idempotency key ──────────────
def test_hard_purge_lets_identical_content_restore(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    # A soft-delete keeps each doc's idempotency key, so re-storing identical content is skipped
    # by `overwrite_mode="ignore"` and the tenant stays empty (the diligence "reset then re-run"
    # failure). A hard purge removes the key, so the same content re-stores and is retrievable.
    ctx = {"tenant_id": "t_reset", "agent_id": "x"}
    store(db, content="quarterly revenue was 12M", **ctx)
    wait_for_searchable(db, query="revenue", **ctx)

    forget(db, tenant_id="t_reset")                              # soft-delete
    assert _eventually_empty(db, "revenue", ctx)
    store(db, content="quarterly revenue was 12M", **ctx)        # same key → ignored
    assert _eventually_empty(db, "revenue", ctx)                 # still gone (the bug)

    purge(db, tenant_id="t_reset")                               # hard-delete → key removed
    store(db, content="quarterly revenue was 12M", **ctx)        # now it lands
    assert wait_for_searchable(db, query="revenue", **ctx).hits


# ── per-memory soft-delete + adapter side rows (CW-1) ──────────────────────
def test_forget_memories_soft_deletes_only_the_named_keys_in_the_tenant(
    db: StandardDatabase,
) -> None:
    ctx = {"tenant_id": "fm_a", "agent_id": "x"}
    keep = store(db, content="keep this one", **ctx).memory_ids[0]
    drop = store(db, content="drop this one", **ctx).memory_ids[0]
    other = store(db, content="drop this one", tenant_id="fm_b", agent_id="x").memory_ids[0]

    # A key from another tenant is ignored even if named.
    assert forget_memories(db, tenant_id="fm_a", memory_keys=[drop, other]) == 1
    memories = db.collection("memories")
    assert memories.get(drop)["invalid_at"] is not None
    assert memories.get(keep)["invalid_at"] is None
    assert memories.get(other)["invalid_at"] is None
    assert forget_memories(db, tenant_id="fm_a", memory_keys=[drop]) == 0  # already gone
    assert forget_memories(db, tenant_id="fm_a", memory_keys=[]) == 0


def test_purge_removes_crewai_side_rows_for_the_subject_only(db: StandardDatabase) -> None:
    rows = db.collection("crewai_records")
    rows.insert({"_key": "p1", "tenant_id": "cp_a", "agent_id": "x", "metadata": {"pii": 1}})
    rows.insert({"_key": "p2", "tenant_id": "cp_b", "agent_id": "x", "metadata": {}})
    counts = purge(db, tenant_id="cp_a")
    assert counts["crewai_records"] == 1
    assert not rows.has("p1") and rows.has("p2")
