"""Fact-augmented keys (FK-1): Dream State stores the facts a memory carries, and retrieval can
match the query against them — a memory that mentions something in passing is found by it."""

from __future__ import annotations

import pytest
from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.config import settings
from arango_memory.generation import FakeGenerator
from arango_memory.ingest.store import store
from arango_memory.lifecycle.dream import run_dream_state
from arango_memory.lifecycle.facts import parse_facts, run_fact_keys
from arango_memory.retrieve.search import force_view_sync, retrieve
from arango_memory.schema.collections import SEARCH_VIEW, ensure_schema

PILLOWS = "user: I need throw pillows for my couch, any shops you'd suggest?"
BAKED = "The user baked a sourdough baguette on 2023/05/20."


def _gen(calls: list[str] | None = None) -> FakeGenerator:
    def handler(prompt: str, system: str | None) -> str:
        if calls is not None:
            calls.append(prompt)
        if "pillows" in prompt:
            return f"- {BAKED}\n2. The user owns a West Elm coffee table"
        return "NONE"

    return FakeGenerator(handler)


def _seed(db: StandardDatabase, tenant: str) -> None:
    store(db, content=PILLOWS, tenant_id=tenant, agent_id="a")
    store(db, content="assistant: try West Elm or IKEA for cushions", tenant_id=tenant,
          agent_id="a")
    store(db, content="user: what is the capital of Peru", tenant_id=tenant, agent_id="a")
    force_view_sync(db, tenant)


def _doc(db: StandardDatabase, tenant: str, text: str) -> dict:
    return next(iter(db.aql.execute(
        "FOR m IN memories FILTER m.tenant_id == @t AND m.text == @x RETURN m",
        bind_vars={"t": tenant, "x": text})))


def test_parse_facts_strips_bullets_and_drops_none() -> None:
    assert parse_facts("- one\n2. two\n\n* three\nNONE", 5) == ["one", "two", "three"]
    assert parse_facts("NONE.", 5) == []
    assert parse_facts("a\nb\nc", 2) == ["a", "b"]


def test_pass_stores_facts_with_embeddings_and_is_incremental(db: StandardDatabase) -> None:
    _seed(db, "t_fk1")
    calls: list[str] = []
    first = run_fact_keys(db, tenant_id="t_fk1", generator=_gen(calls))
    assert (first.processed, first.with_facts, first.facts) == (3, 1, 2)
    doc = _doc(db, "t_fk1", PILLOWS)
    assert doc["facts"] == [BAKED, "The user owns a West Elm coffee table"]
    assert len(doc["fact_embeddings"]) == 2 and doc["facts_extracted_at"]
    assert _doc(db, "t_fk1", "user: what is the capital of Peru").get("facts") is None
    assert run_fact_keys(db, tenant_id="t_fk1", generator=_gen(calls)).processed == 0
    assert len(calls) == 3


def test_prefix_limits_which_memories_are_mined(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db, "t_fk2")
    monkeypatch.setattr(settings, "fact_keys_prefix", "user:")
    calls: list[str] = []
    assert run_fact_keys(db, tenant_id="t_fk2", generator=_gen(calls)).processed == 2
    assert not any("IKEA" in c for c in calls)


def test_message_date_reaches_the_prompt(db: StandardDatabase) -> None:
    store(db, content=PILLOWS, tenant_id="t_fk3", agent_id="a", event_time="2023/05/30 (Tue)")
    calls: list[str] = []
    run_fact_keys(db, tenant_id="t_fk3", generator=_gen(calls))
    assert calls[0].startswith("Message date: 2023/05/30 (Tue)")


def test_retrieval_matches_facts_only_when_asked(db: StandardDatabase) -> None:
    _seed(db, "t_fk4")
    run_fact_keys(db, tenant_id="t_fk4", generator=_gen())
    force_view_sync(db, "t_fk4")
    query = "sourdough baguette baked"
    off = retrieve(db, query=query, tenant_id="t_fk4", agent_id="a", k=1)
    assert PILLOWS not in [h.text for h in off.hits]
    on = retrieve(db, query=query, tenant_id="t_fk4", agent_id="a", k=1, fact_keys=True)
    assert [h.text for h in on.hits] == [PILLOWS]
    assert {"fact_bm25", "fact_vector"} & set(on.hits[0].source.split("+"))


def test_setting_turns_the_arms_on(db: StandardDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(db, "t_fk5")
    run_fact_keys(db, tenant_id="t_fk5", generator=_gen())
    force_view_sync(db, "t_fk5")
    monkeypatch.setattr(settings, "retrieve_fact_keys", True)
    result = retrieve(db, query="sourdough baguette baked", tenant_id="t_fk5", agent_id="a", k=1)
    assert [h.text for h in result.hits] == [PILLOWS]


def test_vector_arm_is_skipped_above_the_scan_cap(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db, "t_fk6")
    run_fact_keys(db, tenant_id="t_fk6", generator=_gen())
    force_view_sync(db, "t_fk6")
    monkeypatch.setattr(settings, "fact_vector_scan_max", 1)
    result = retrieve(db, query="sourdough baguette baked", tenant_id="t_fk6", agent_id="a",
                      k=1, fact_keys=True)
    assert "fact_vector" not in result.hits[0].source.split("+")
    assert "fact_bm25" in result.hits[0].source.split("+")


def test_dream_runs_the_pass_only_when_enabled(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db, "t_fk7")
    run_dream_state(db, tenant_id="t_fk7", generator=_gen())
    assert _doc(db, "t_fk7", PILLOWS).get("facts") is None
    monkeypatch.setattr(settings, "fact_keys", True)
    run_dream_state(db, tenant_id="t_fk7", generator=_gen())
    assert _doc(db, "t_fk7", PILLOWS)["facts"][0] == BAKED


def test_api_option(db: StandardDatabase, api: TestClient) -> None:
    _seed(db, "t_fk8")
    run_fact_keys(db, tenant_id="t_fk8", generator=_gen())
    force_view_sync(db, "t_fk8")
    body = {"query": "sourdough baguette baked", "ctx": {"tenant_id": "t_fk8", "agent_id": "a"},
            "opts": {"k": 1, "fact_keys": True}}
    resp = api.post("/v1/retrieve", json=body)
    assert resp.status_code == 200
    assert [h["text"] for h in resp.json()["hits"]] == [PILLOWS]


def test_an_older_search_view_gains_facts_and_keeps_its_other_fields(
    db: StandardDatabase,
) -> None:
    db.delete_view(SEARCH_VIEW)
    db.create_arangosearch_view(
        SEARCH_VIEW,
        {"links": {"memories": {"fields": {
            "text": {"analyzers": ["text_en"]}, "tenant_id": {"analyzers": ["identity"]},
            "agent_id": {"analyzers": ["identity"]}}}}},
    )
    ensure_schema(db)
    fields = db.view(SEARCH_VIEW)["links"]["memories"]["fields"]
    assert {"text", "prospective_queries", "facts", "tenant_id", "agent_id"} <= set(fields)
    store(db, content="user: I planted tulips", tenant_id="t_fk9", agent_id="a")
    force_view_sync(db, "t_fk9")
    assert retrieve(db, query="tulips", tenant_id="t_fk9", agent_id="a", k=1).hits


class _SpyReranker:
    def __init__(self) -> None:
        self.docs: list[str] = []

    def score(self, query: str, texts: list[str]) -> list[float]:
        self.docs = list(texts)
        return [1.0 if "baguette" in t else 0.0 for t in texts]


def test_reranker_sees_the_facts_with_the_text(db: StandardDatabase) -> None:
    _seed(db, "t_fk10")
    run_fact_keys(db, tenant_id="t_fk10", generator=_gen())
    force_view_sync(db, "t_fk10")
    spy = _SpyReranker()
    result = retrieve(db, query="sourdough baguette baked", tenant_id="t_fk10", agent_id="a",
                      k=1, fact_keys=True, rerank=True, reranker=spy)
    assert any(BAKED in d and PILLOWS in d for d in spy.docs)
    assert [h.text for h in result.hits] == [PILLOWS]
    plain = _SpyReranker()
    retrieve(db, query="sourdough baguette baked", tenant_id="t_fk10", agent_id="a", k=1,
             rerank=True, reranker=plain)
    assert all(BAKED not in d for d in plain.docs)
