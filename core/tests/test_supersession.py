"""Memory-level fact supersession + point-in-time retrieval (GX-2, DESIGN.md §12).

Dream State links a memory to the older statements it updates; retrieval ranks the current
statement above a stale one and marks the stale line, and `as_of` views memory at a past time.
"""

from __future__ import annotations

import pytest
from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.config import settings
from arango_memory.generation import FakeGenerator
from arango_memory.ingest.store import store
from arango_memory.lifecycle.dream import run_dream_state
from arango_memory.lifecycle.supersession import _parse, run_supersession
from arango_memory.retrieve.search import force_view_sync, retrieve

JAN, FEB, MAR = "2023-01-10T00:00:00Z", "2023-02-10T00:00:00Z", "2023-03-10T00:00:00Z"


@pytest.fixture(autouse=True)
def _any_similarity(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fake embeddings don't model meaning; gate candidates on the shared entity alone.
    monkeypatch.setattr(settings, "supersession_min_similarity", -1.0)


def _put(db: StandardDatabase, t: str, text: str, when: str, agent: str = "a") -> str:
    return store(db, content=text, tenant_id=t, agent_id=agent, event_time=when).memory_ids[0]


def _doc(db: StandardDatabase, key: str) -> dict:
    return db.collection("memories").get(key)


def _answers(reply: str) -> tuple[FakeGenerator, list[str]]:
    prompts: list[str] = []

    def handler(prompt: str, system: str | None) -> str:
        prompts.append(prompt)
        return reply

    return FakeGenerator(handler=handler), prompts


def test_parse_reply() -> None:
    assert _parse("NONE", 3) == []
    assert _parse("1, 3", 3) == [1, 3]
    assert _parse("2 and 9", 3) == [2]  # out of range ignored


# ── Dream State pass ──────────────────────────────────────
def test_links_the_older_statement_and_is_incremental(db: StandardDatabase) -> None:
    t = "t_ss1"
    boston = _put(db, t, "Sam lives in Boston", JAN)
    denver = _put(db, t, "Sam moved to Denver", MAR)
    gen, prompts = _answers("1")
    result = run_supersession(db, tenant_id=t, generator=gen)
    assert (result.checked, result.compared, result.superseded) == (2, 1, 1)
    assert prompts == ["NEW: Sam moved to Denver\nOLDER:\n1. Sam lives in Boston"]
    old = _doc(db, boston)
    assert old["superseded_by"] == denver and old["valid_to"] == MAR
    assert old["invalid_at"] is None  # nothing hidden
    assert _doc(db, denver)["supersession_checked_at"] is not None

    again = run_supersession(db, tenant_id=t, generator=gen)
    assert again.checked == 0 and len(prompts) == 1


def test_none_verdict_links_nothing(db: StandardDatabase) -> None:
    t = "t_ss2"
    boston = _put(db, t, "Sam lives in Boston", JAN)
    _put(db, t, "Sam likes Denver jazz", MAR)
    gen, _ = _answers("NONE")
    assert run_supersession(db, tenant_id=t, generator=gen).superseded == 0
    assert _doc(db, boston).get("superseded_by") is None


def test_other_agents_and_same_time_are_not_compared(db: StandardDatabase) -> None:
    t = "t_ss3"
    _put(db, t, "Sam lives in Boston", JAN, agent="other")
    _put(db, t, "Sam lives in Austin", MAR)
    _put(db, t, "Sam visits Austin", MAR)  # same time: neither is older
    gen, prompts = _answers("1")
    run_supersession(db, tenant_id=t, generator=gen)
    assert prompts == []


def test_chain_links_each_statement_to_the_next(db: StandardDatabase) -> None:
    t = "t_ss4"
    boston = _put(db, t, "Sam lives in Boston", JAN)
    chicago = _put(db, t, "Sam moved to Chicago", FEB)
    denver = _put(db, t, "Sam moved to Denver", MAR)
    gen, _ = _answers("1")
    run_supersession(db, tenant_id=t, generator=gen)
    assert _doc(db, boston)["superseded_by"] == chicago
    assert _doc(db, chicago)["superseded_by"] == denver


def test_dream_runs_it_only_when_enabled(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = "t_ss5"
    _put(db, t, "Sam lives in Boston", JAN)
    _put(db, t, "Sam moved to Denver", MAR)
    gen, prompts = _answers("1")
    assert run_dream_state(db, tenant_id=t, generator=gen).memories_superseded == 0
    assert prompts == []
    monkeypatch.setattr(settings, "fact_supersession", True)
    assert run_dream_state(db, tenant_id=t, generator=gen).memories_superseded == 1


# ── retrieval ─────────────────────────────────────────────
def _linked(db: StandardDatabase, t: str) -> tuple[str, str, str]:
    """Boston → Chicago → Denver, linked as Dream State would, plus an unrelated memory."""
    boston = _put(db, t, "Sam lives in Boston near the harbour", JAN)
    chicago = _put(db, t, "Sam moved to Chicago", FEB)
    denver = _put(db, t, "Sam moved to Denver", MAR)
    _put(db, t, "Lunch was pasta at the harbour", JAN)
    mem = db.collection("memories")
    mem.update({"_key": boston, "superseded_by": chicago, "valid_to": FEB})
    mem.update({"_key": chicago, "superseded_by": denver, "valid_to": MAR})
    force_view_sync(db, t)
    return boston, chicago, denver


def _texts(db: StandardDatabase, t: str, **kw: object) -> list[str]:
    result = retrieve(db, query="Boston harbour", tenant_id=t, agent_id="a", k=2, **kw)
    return [h.text for h in result.hits]


def test_off_by_default_leaves_the_ranking_alone(db: StandardDatabase) -> None:
    _linked(db, "t_ss6")
    result = retrieve(db, query="Boston harbour", tenant_id="t_ss6", agent_id="a", k=2)
    assert result.hits[0].text.startswith("Sam lives in Boston")
    assert "superseded" not in result.context


def test_pulls_the_current_statement_above_the_stale_one(db: StandardDatabase) -> None:
    _linked(db, "t_ss7")
    result = retrieve(db, query="Boston harbour", tenant_id="t_ss7", agent_id="a", k=2,
                      supersession=True)
    texts = [h.text for h in result.hits]
    # Chain followed to the statement that is current now (Denver, not Chicago); k kept.
    assert texts == ["Sam moved to Denver", "Sam lives in Boston near the harbour"]
    assert f"(superseded {FEB}) Sam lives in Boston" in result.context
    assert "supersession" in result.hits[0].source


def test_as_of_views_memory_at_a_past_time(db: StandardDatabase) -> None:
    _linked(db, "t_ss8")
    # Mid-January: only January memories exist and Boston was still current.
    jan = retrieve(db, query="Boston harbour", tenant_id="t_ss8", agent_id="a", k=3,
                   supersession=True, as_of="2023-01-20")
    assert {h.text for h in jan.hits} == {"Sam lives in Boston near the harbour",
                                          "Lunch was pasta at the harbour"}
    assert "superseded" not in jan.context
    # Mid-February: Chicago is the current statement then, not Denver.
    feb = _texts(db, "t_ss8", supersession=True, as_of="2023-02-20")
    assert feb[:2] == ["Sam moved to Chicago", "Sam lives in Boston near the harbour"]


def test_bad_as_of_is_a_caller_error(db: StandardDatabase, api: TestClient) -> None:
    with pytest.raises(ValueError, match="as_of"):
        retrieve(db, query="x", tenant_id="t_ss9", agent_id="a", as_of="last tuesday")
    r = api.post("/v1/retrieve", json={
        "query": "x", "ctx": {"tenant_id": "t_ss9", "agent_id": "a"},
        "opts": {"as_of": "last tuesday"},
    })
    assert r.status_code == 422


def test_similarity_gate_compares_without_a_shared_entity(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "27 birds" → "32 birds": no shared named entity, so the entity gate never offers the pair.
    t = "t_ss10"
    old = _put(db, t, "my count of yard birds is twenty seven", JAN)
    new = _put(db, t, "my count of yard birds is now thirty two", MAR)
    gen, prompts = _answers("1")
    run_supersession(db, tenant_id=t, generator=gen)
    assert prompts == []  # entity gate (default): no candidates

    db.aql.execute("FOR m IN memories FILTER m.tenant_id == @t "
                   "UPDATE m WITH { supersession_checked_at: null } IN memories",
                   bind_vars={"t": t})
    monkeypatch.setattr(settings, "supersession_gate", "similarity")
    result = run_supersession(db, tenant_id=t, generator=gen)
    assert result.superseded == 1
    assert _doc(db, old)["superseded_by"] == new
