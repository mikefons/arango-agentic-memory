"""Corroboration count + source reliability → belief (DESIGN.md §8, §12)."""

from __future__ import annotations

import time

from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.generation import FakeGenerator
from arango_memory.ingest.store import store
from arango_memory.lifecycle.dream import run_dream_state


def _entity(db: StandardDatabase, tenant: str, name: str) -> dict[str, object]:
    return next(
        db.aql.execute(
            "FOR e IN entities FILTER e.tenant_id == @t AND e.name == @n RETURN e",
            bind_vars={"t": tenant, "n": name},
        )
    )


# ── belief from corroboration ─────────────────────────────
def test_belief_rises_with_corroboration(db: StandardDatabase) -> None:
    for i in range(3):
        store(db, content=f"Alice appears in chamber {i}", tenant_id="cc1", agent_id="a")
    alice = _entity(db, "cc1", "Alice")
    assert alice["mention_count"] == 3
    # belief = confidence(1.0) × (1 − 0.5^3) = 0.875
    assert alice["belief"] > 0.85
    assert alice["confidence"] == 1.0  # source prior unchanged


def test_source_reliability_dampens_belief(db: StandardDatabase) -> None:
    store(db, content="Bravo speaks", tenant_id="cc2", agent_id="a", source_reliability=0.2)
    store(db, content="Charlie speaks", tenant_id="cc2", agent_id="a", source_reliability=1.0)
    low = _entity(db, "cc2", "Bravo")
    high = _entity(db, "cc2", "Charlie")
    assert low["belief"] < high["belief"]  # unreliable source corroborates less


# ── relation corroboration ────────────────────────────────
def test_relation_corroboration_increments(db: StandardDatabase) -> None:
    for i in range(2):
        store(db, content=f"Delta meets Echo at dusk {i}", tenant_id="cc3", agent_id="a")
    delta = _entity(db, "cc3", "Delta")
    corr = list(
        db.aql.execute(
            "FOR e IN relates_to FILTER e._from == @d OR e._to == @d RETURN e.corroboration",
            bind_vars={"d": f"entities/{delta['_key']}"},
        )
    )
    assert corr and max(corr) == 2  # the Delta↔Echo relation asserted by 2 episodes


# ── conflict-resolution tiebreaker ────────────────────────
def test_dream_tiebreaker_keeps_better_attested(db: StandardDatabase) -> None:
    ents = db.collection("entities")
    common = {"tenant_id": "cc4", "agent_id": "a", "label": "Concept", "confidence": 1.0,
              "summary": "", "consolidated_at": None, "invalid_at": None}
    ents.insert({"_key": "weak", "name": "WeakClaim", "mention_count": 1, "belief": 0.3,
                 "reliability_sum": 0.5, "needs_review": True, "conflict_with": "strong", **common})
    ents.insert({"_key": "strong", "name": "StrongClaim", "mention_count": 5, "belief": 0.95,
                 "reliability_sum": 5.0, "needs_review": False, "conflict_with": None, **common})

    gen = FakeGenerator(lambda prompt, system: "CONTRADICTS")
    # high mention threshold → only the flagged 'weak' is a candidate; breaker off
    run_dream_state(
        db, tenant_id="cc4", generator=gen, mention_threshold=100, breaker_threshold=1.0
    )

    assert ents.get("weak")["invalid_at"] is not None  # lower-belief superseded
    assert ents.get("strong")["invalid_at"] is None     # better-attested survives


# ── surfaced in reads ─────────────────────────────────────
def test_graph_and_entity_reads_expose_belief(api: TestClient) -> None:
    ctx = {"tenant_id": "cc5", "agent_id": "a", "access_level": "write"}
    api.post("/v1/store", json={"content": "Foxtrot guards Golf", "ctx": ctx})

    graph: dict[str, list[dict[str, object]]] = {"nodes": [], "edges": []}
    for _ in range(20):
        graph = api.get("/v1/graph", params={"tenant_id": "cc5"}).json()
        if graph["nodes"] and graph["edges"]:
            break
        time.sleep(0.25)

    assert all("belief" in n for n in graph["nodes"])
    assert any(e.get("corroboration") for e in graph["edges"])
    node_id = graph["nodes"][0]["id"]
    one = api.get("/v1/entity", params={"entity_id": node_id, "tenant_id": "cc5"}).json()
    assert "belief" in one["entity"]
