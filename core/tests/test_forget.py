"""Right-to-be-forgotten (soft-delete + purge) and ABAC enforcement (DESIGN.md §17)."""

from __future__ import annotations

import time
from collections.abc import Callable

from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult, retrieve
from arango_memory.security.forget import forget, purge


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
