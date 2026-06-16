"""EWA edge weight — recency-decayed relation strength (DESIGN.md §12)."""

from __future__ import annotations

import time
from collections.abc import Callable

from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.config import settings
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult


def _edge(db: StandardDatabase, tenant: str, a: str, b: str) -> dict:
    return next(
        db.aql.execute(
            """
            FOR edge IN relates_to
              LET f = DOCUMENT(edge._from)
              LET t = DOCUMENT(edge._to)
              FILTER f.tenant_id == @tenant
                 AND ((f.name == @a AND t.name == @b) OR (f.name == @b AND t.name == @a))
              RETURN edge
            """,
            bind_vars={"tenant": tenant, "a": a, "b": b},
        )
    )


def test_weight_seeds_at_alpha(db: StandardDatabase) -> None:
    store(db, content="Alice meets Bob", tenant_id="ewa1", agent_id="a")
    edge = _edge(db, "ewa1", "Alice", "Bob")
    assert edge["weight"] == settings.weight_ewa_alpha


def test_corroboration_raises_weight(db: StandardDatabase) -> None:
    store(db, content="Alice meets Bob", turn_index=0, tenant_id="ewa2", agent_id="a")
    store(db, content="Alice meets Bob", turn_index=1, tenant_id="ewa2", agent_id="a")
    edge = _edge(db, "ewa2", "Alice", "Bob")
    # α=0.5: second confirmation → 0.5 + 0.5·0.5 ≈ 0.75 (Δt≈0)
    assert edge["weight"] > settings.weight_ewa_alpha


def test_stale_confirmation_decays_below_fresh(db: StandardDatabase) -> None:
    # Fresh: two back-to-back confirmations.
    store(db, content="Cara meets Dan", turn_index=0, tenant_id="ewa3", agent_id="a")
    store(db, content="Cara meets Dan", turn_index=1, tenant_id="ewa3", agent_id="a")
    fresh = _edge(db, "ewa3", "Cara", "Dan")["weight"]

    # Stale: age the edge before the second confirmation.
    store(db, content="Cara meets Dan", turn_index=0, tenant_id="ewa4", agent_id="a")
    edge = _edge(db, "ewa4", "Cara", "Dan")
    from datetime import UTC, datetime, timedelta

    old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    db.collection("relates_to").update({"_key": edge["_key"], "last_seen": old})
    store(db, content="Cara meets Dan", turn_index=1, tenant_id="ewa4", agent_id="a")
    stale = _edge(db, "ewa4", "Cara", "Dan")["weight"]

    assert stale < fresh  # time decay dampens a long-dormant relation


def test_graph_edge_exposes_weight(api: TestClient) -> None:
    ctx = {"tenant_id": "ewa5", "agent_id": "a", "access_level": "write"}
    api.post("/v1/store", json={"content": "Eve meets Finn", "ctx": ctx})
    edges: list[dict] = []
    for _ in range(20):
        edges = api.get("/v1/graph", params={"tenant_id": "ewa5"}).json()["edges"]
        if edges:
            break
        time.sleep(0.25)
    assert edges and all("weight" in e for e in edges if e["kind"] == "relates_to")


def test_graph_retrieval_still_works(
    db: StandardDatabase, wait_for_searchable: Callable[..., RetrieveResult]
) -> None:
    # The EWA weight is folded into the graph bridge salience — retrieval must
    # still surface connected memories (no regression).
    ctx = {"tenant_id": "ewa6", "agent_id": "a"}
    store(db, content="Gail knows Hugo", turn_index=0, **ctx)
    store(db, content="Hugo guards the vault", turn_index=1, **ctx)
    result = wait_for_searchable(db, query="Gail", **ctx)
    assert result.hits
