"""Ontology evolution — relationship-type proposals + approval (DESIGN.md §13)."""

from __future__ import annotations

import pytest
from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.config import settings
from arango_memory.generation import FakeGenerator
from arango_memory.lifecycle.ontology import (
    approve_proposal,
    list_proposals,
    propose_relationship_types,
    reject_proposal,
)


def _gen(reply: str) -> FakeGenerator:
    return FakeGenerator(handler=lambda prompt, system: reply)


def _entity(db: StandardDatabase, key: str, name: str, label: str, tenant: str) -> None:
    db.collection("entities").insert(
        {"_key": key, "name": name, "label": label, "tenant_id": tenant, "invalid_at": None}
    )


def _assoc(db: StandardDatabase, a: str, b: str) -> None:
    lo, hi = sorted((a, b))
    db.collection("relates_to").insert(
        {"_key": f"{lo}__{hi}", "_from": f"entities/{lo}", "_to": f"entities/{hi}",
         "relationship": "associated_with"}
    )


def _seed_person_company(db: StandardDatabase, tenant: str, n: int = 3) -> None:
    for i in range(n):
        p, c = f"{tenant}_p{i}", f"{tenant}_c{i}"
        _entity(db, p, f"Person{i}", "Person", tenant)
        _entity(db, c, f"Company{i}", "Company", tenant)
        _assoc(db, p, c)


# ── proposal pass ─────────────────────────────────────────
def test_proposes_type_for_recurring_cluster(db: StandardDatabase) -> None:
    t = "t_onto_1"
    _seed_person_company(db, t, n=3)
    result = propose_relationship_types(db, tenant_id=t, generator=_gen("works_at"))
    assert result == {"clusters": 1, "proposed": 1}

    proposals = list_proposals(db, tenant_id=t)
    assert len(proposals) == 1
    assert proposals[0]["proposed_relationship"] == "works_at"
    assert proposals[0]["status"] == "pending"
    assert proposals[0]["support"] == 3


def test_below_min_support_is_ignored(db: StandardDatabase) -> None:
    t = "t_onto_2"
    _seed_person_company(db, t, n=2)  # below default min_support of 3
    result = propose_relationship_types(db, tenant_id=t, generator=_gen("works_at"))
    assert result["proposed"] == 0


def test_none_reply_proposes_nothing(db: StandardDatabase) -> None:
    t = "t_onto_3"
    _seed_person_company(db, t, n=3)
    result = propose_relationship_types(db, tenant_id=t, generator=_gen("NONE"))
    assert result == {"clusters": 1, "proposed": 0}
    assert list_proposals(db, tenant_id=t) == []


def test_proposal_upsert_is_idempotent(db: StandardDatabase) -> None:
    t = "t_onto_4"
    _seed_person_company(db, t, n=3)
    propose_relationship_types(db, tenant_id=t, generator=_gen("works_at"))
    propose_relationship_types(db, tenant_id=t, generator=_gen("employed_by"))
    proposals = list_proposals(db, tenant_id=t)
    assert len(proposals) == 1  # same label-pair → one proposal, updated in place
    assert proposals[0]["proposed_relationship"] == "employed_by"


# ── approve / reject ──────────────────────────────────────
def _rel(db: StandardDatabase, a: str, b: str) -> str:
    lo, hi = sorted((a, b))
    return db.collection("relates_to").get(f"{lo}__{hi}")["relationship"]


def test_approve_relabels_matching_edges(db: StandardDatabase) -> None:
    t = "t_onto_5"
    _seed_person_company(db, t, n=3)
    propose_relationship_types(db, tenant_id=t, generator=_gen("works_at"))
    key = list_proposals(db, tenant_id=t)[0]["_key"]

    result = approve_proposal(db, tenant_id=t, key=key)
    assert result["status"] == "approved"
    assert result["relabeled"] == 3
    assert _rel(db, f"{t}_p0", f"{t}_c0") == "works_at"
    assert list_proposals(db, tenant_id=t, status="approved")[0]["_key"] == key


def test_reject_leaves_graph_untouched(db: StandardDatabase) -> None:
    t = "t_onto_6"
    _seed_person_company(db, t, n=3)
    propose_relationship_types(db, tenant_id=t, generator=_gen("works_at"))
    key = list_proposals(db, tenant_id=t)[0]["_key"]

    assert reject_proposal(db, tenant_id=t, key=key)["status"] == "rejected"
    assert _rel(db, f"{t}_p0", f"{t}_c0") == "associated_with"  # unchanged
    assert list_proposals(db, tenant_id=t, status="rejected")[0]["_key"] == key


def test_approve_is_tenant_scoped(db: StandardDatabase) -> None:
    t = "t_onto_7"
    _seed_person_company(db, t, n=3)
    propose_relationship_types(db, tenant_id=t, generator=_gen("works_at"))
    key = list_proposals(db, tenant_id=t)[0]["_key"]
    assert approve_proposal(db, tenant_id="other", key=key)["status"] == "not_found"


# ── endpoints + flag gating ───────────────────────────────
def test_scan_404_when_flag_off(api: TestClient) -> None:
    ctx = {"tenant_id": "t_onto_e1", "agent_id": "a", "access_level": "write"}
    assert api.post("/v1/ontology/scan", json={"ctx": ctx}).status_code == 404


def test_endpoints_when_flag_on(
    api: TestClient, db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ontology_evolution", True)
    t = "t_onto_e2"
    _seed_person_company(db, t, n=3)
    # seed a proposal directly (the app's Fake generator proposes nothing)
    db.collection("ontology_proposals").insert(
        {"_key": f"{t}__Company__Person", "tenant_id": t, "label_a": "Company",
         "label_b": "Person", "proposed_relationship": "works_at", "support": 3,
         "examples": [], "status": "pending"}
    )
    ctx = {"tenant_id": t, "agent_id": "a", "access_level": "write"}

    assert api.post("/v1/ontology/scan", json={"ctx": ctx}).status_code == 200
    listed = api.get("/v1/ontology/proposals", params={"tenant_id": t}).json()
    assert any(p["proposed_relationship"] == "works_at" for p in listed)

    res = api.post(
        "/v1/ontology/approve", json={"ctx": ctx, "key": f"{t}__Company__Person"}
    )
    assert res.status_code == 200 and res.json()["relabeled"] == 3


def test_scan_requires_write(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ontology_evolution", True)
    ctx = {"tenant_id": "t_onto_e3", "agent_id": "a", "access_level": "read"}
    assert api.post("/v1/ontology/scan", json={"ctx": ctx}).status_code == 403
