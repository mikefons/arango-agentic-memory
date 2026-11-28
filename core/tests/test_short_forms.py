"""Merging short entity names into the one longer name they begin (`lifecycle.short_forms`)."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.config import ApiKeyEntry, settings
from arango_memory.entity_api import list_entities
from arango_memory.generation import FakeGenerator
from arango_memory.graph_api import tenant_graph
from arango_memory.ingest.store import StoreItem, store, store_many
from arango_memory.lifecycle import dream
from arango_memory.lifecycle.dream import run_dream_state
from arango_memory.lifecycle.short_forms import merge_report, merge_short_forms, plan_merges
from arango_memory.schema import collections
from arango_memory.schema.collections import has_vector_index
from arango_memory.security.forget import forget_memories, purge
from arango_memory.transport import ObservedHTTPClient

from .conftest import StubEmbedder


@pytest.mark.parametrize(
    ("names", "planned"),
    [
        # one longer name begins with the short one
        (["Ridgeline", "Ridgeline Ventures"], {"Ridgeline": "Ridgeline Ventures"}),
        (["Halden", "Halden Technical Advisory"], {"Halden": "Halden Technical Advisory"}),
        (["Acme Inc.", "Acme Robotics, LLC"], {"Acme Inc.": "Acme Robotics, LLC"}),
        # two do, so it stays
        (["Northwind", "Northwind Robotics", "Northwind Logistics LLC"], {}),
        # a surname is not how the longer name begins
        (["Cole", "Marcus Cole"], {}),
        # whole words only
        (["North", "Northwind Robotics"], {}),
        # an acronym or a lower-case word makes the longer name something else, which then
        # doesn't count against a fuller form either
        (["Northwind", "Northwind ARR"], {}),
        (["Halcyon", "Halcyon deal"], {}),
        (["Halcyon", "Halcyon Grocers", "Halcyon Grocers deal"],
         {"Halcyon": "Halcyon Grocers"}),
        # only a single word is a short form
        (["Northwind Robotics", "Northwind Robotics Europe"], {}),
        # an article is not a short form
        (["The", "The Superior Court"], {}),
    ],
)
def test_plan(names: list[str], planned: dict[str, str]) -> None:
    rows = [{"key": name, "name": name} for name in names]
    assert dict(plan_merges(rows)) == planned


def _entity(db: StandardDatabase, tenant: str, name: str) -> dict[str, Any]:
    return next(db.aql.execute(  # type: ignore[arg-type, return-value]
        "FOR e IN entities FILTER e.tenant_id == @t AND e.name == @n RETURN e",
        bind_vars={"t": tenant, "n": name},
    ))


def _edges(db: StandardDatabase, collection: str, entity_id: str) -> list[dict[str, Any]]:
    return list(db.aql.execute(  # type: ignore[arg-type]
        f"FOR e IN {collection} FILTER e._from == @id OR e._to == @id RETURN e",
        bind_vars={"id": entity_id},
    ))


_TURNS = (
    "Ridgeline Ventures backs Orion.",
    "then Ridgeline met Orion.",
    "later Ridgeline hired Dana.",
)


def _write(db: StandardDatabase, tenant: str, *turns: str) -> None:
    for i, text in enumerate(turns or _TURNS):
        store(db, content=text, tenant_id=tenant, agent_id="a", session_id=str(hash(text)),
              turn_index=i)


def _assert_merged(db: StandardDatabase, tenant: str) -> None:
    short = _entity(db, tenant, "Ridgeline")
    long = _entity(db, tenant, "Ridgeline Ventures")
    orion = _entity(db, tenant, "Orion")
    assert short["merged_into"] == long["_key"] and short["invalid_at"]
    assert (long["mention_count"], long["reliability_sum"]) == (3, 3.0)
    assert long["belief"] == pytest.approx(1 - 0.5**3)
    for collection in ("mentions", "produced_by", "relates_to"):
        assert _edges(db, collection, short["_id"]) == []
    assert len(_edges(db, "mentions", long["_id"])) == 3
    assert len(_edges(db, "produced_by", long["_id"])) == 3
    # the two Ridgeline-Orion relations are one now, carrying both episodes
    joined = [e for e in _edges(db, "relates_to", long["_id"])
              if orion["_id"] in (e["_from"], e["_to"])]
    assert [(e["corroboration"], e["reliability_sum"]) for e in joined] == [(2, 2.0)]
    assert joined[0]["_key"] == "__".join(sorted((long["_key"], orion["_key"])))


def test_merge_moves_counts_and_edges(db: StandardDatabase) -> None:
    _write(db, "t")
    assert merge_short_forms(db, tenant_id="t") == 1
    _assert_merged(db, "t")
    names = {e["name"] for e in list_entities(db, tenant_id="t")}
    assert "Ridgeline Ventures" in names and "Ridgeline" not in names
    assert "Ridgeline" not in {n["name"] for n in tenant_graph(db, tenant_id="t")["nodes"]}
    assert merge_short_forms(db, tenant_id="t") == 0
    _assert_merged(db, "t")


def test_a_relation_between_the_two_is_dropped(db: StandardDatabase) -> None:
    _write(db, "t", "Ridgeline, also written Ridgeline Ventures.")
    merge_short_forms(db, tenant_id="t")
    long = _entity(db, "t", "Ridgeline Ventures")
    assert _edges(db, "relates_to", long["_id"]) == []


def test_other_tenants_are_left_alone(db: StandardDatabase) -> None:
    _write(db, "t")
    _write(db, "u", "then Ridgeline met Orion.")
    _write(db, "u2")
    assert merge_short_forms(db, tenant_id="t") == 1
    assert _entity(db, "u", "Ridgeline")["invalid_at"] is None
    assert _entity(db, "u2", "Ridgeline")["invalid_at"] is None


@pytest.mark.parametrize("cache_mb", [0, 64])  # both forms of the tenant scan
@pytest.mark.parametrize("batched", [False, True])
def test_later_writes_of_the_short_name_reach_the_longer_one(
    db: StandardDatabase, batched: bool, cache_mb: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "entity_vector_cache_mb", cache_mb)
    _write(db, "t")
    merge_short_forms(db, tenant_id="t")
    text = "now Ridgeline sold Orion."
    if batched:
        result = store_many(db, [StoreItem(content=text, turn_index=9)], extract=True,
                            tenant_id="t", agent_id="a")
        keys = result[0].entity_ids
    else:
        keys = store(db, content=text, tenant_id="t", agent_id="a", turn_index=9).entity_ids
    short = _entity(db, "t", "Ridgeline")
    long = _entity(db, "t", "Ridgeline Ventures")
    assert long["_key"] in keys and short["_key"] not in keys
    assert long["mention_count"] == 4 and short["mention_count"] == 2
    assert _edges(db, "mentions", short["_id"]) == []


@pytest.mark.parametrize("batched", [False, True])
def test_the_vector_index_path_follows_a_merge_too(
    db: StandardDatabase, batched: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collections, "VECTOR_STATE_TTL_SECONDS", 0.0)
    monkeypatch.setattr(settings, "entity_vector_n_lists", 2)
    monkeypatch.setattr(settings, "entity_vector_train_factor", 2)
    monkeypatch.setattr(settings, "entity_resolution_scan_max", 0)
    names = ["Ridgeline Ventures", "Ridgeline", "Bravo", "Cara", "Delta", "Echo"]
    axes = [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0], [-1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]]
    emb = StubEmbedder({
        **dict(zip(names, axes, strict=True)),
        "Ridgelyne": [0.24, 0.97, 0.0],  # nearest the short name: the same thing to the index
    })
    for i, name in enumerate(names):
        store(db, content=name, tenant_id="t", agent_id="a", turn_index=i, embedder=emb)
    assert has_vector_index(db, "entities")
    assert merge_short_forms(db, tenant_id="t") == 1

    for i, name in enumerate(["Ridgeline", "Ridgelyne"]):  # by name, then by likeness
        if batched:
            store_many(db, [StoreItem(content=name, turn_index=10 + i)], extract=True,
                       tenant_id="t", agent_id="a", embedder=emb)
        else:
            store(db, content=name, tenant_id="t", agent_id="a", turn_index=10 + i,
                  embedder=emb)
    assert _entity(db, "t", "Ridgeline Ventures")["mention_count"] == 4
    assert _entity(db, "t", "Ridgeline")["mention_count"] == 1
    assert db.collection("entities").count() == len(names)


def test_a_write_that_reached_the_short_entity_late_is_moved(db: StandardDatabase) -> None:
    _write(db, "t", *_TURNS[:2])
    merge_short_forms(db, tenant_id="t")
    short = _entity(db, "t", "Ridgeline")
    # a writer that resolved the name before the merge finishes after it
    _write(db, "t", "later Ridgeline Ventures hired Dana.")
    long = _entity(db, "t", "Ridgeline Ventures")
    memory = next(db.aql.execute("FOR m IN memories LIMIT 1 RETURN m._key"))  # type: ignore[arg-type]
    db.collection("entities").update(
        {"_key": short["_key"], "mention_count": 2, "reliability_sum": 2.0})
    db.collection("mentions").insert(
        {"_key": f"late__{short['_key']}", "_from": f"memories/{memory}", "_to": short["_id"],
         "tenant_id": "t"})
    merge_short_forms(db, tenant_id="t")
    after = _entity(db, "t", "Ridgeline Ventures")
    assert after["mention_count"] == long["mention_count"] + 1
    assert _edges(db, "mentions", short["_id"]) == []
    merge_short_forms(db, tenant_id="t")
    assert _entity(db, "t", "Ridgeline Ventures")["mention_count"] == after["mention_count"]


def test_requests_sent_twice_merge_once(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(db, "t")
    real = ObservedHTTPClient.send_request

    def twice(self: ObservedHTTPClient, session: Any, method: str, url: str, *args: Any,
              **kwargs: Any) -> Any:
        if method.lower() == "post" and url.endswith("/_api/cursor"):
            real(self, session, method, url, *args, **kwargs)
        return real(self, session, method, url, *args, **kwargs)

    monkeypatch.setattr(ObservedHTTPClient, "send_request", twice)
    merge_short_forms(db, tenant_id="t")
    _assert_merged(db, "t")


def test_a_pass_cut_short_at_any_query_is_finished_by_the_next(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = ObservedHTTPClient.send_request
    for allowed in range(1, 60):
        tenant = f"t{allowed}"
        _write(db, tenant)
        sent = 0

        def cut(self: ObservedHTTPClient, session: Any, method: str, url: str, *args: Any,
                limit: int = allowed, **kwargs: Any) -> Any:
            nonlocal sent
            if method.lower() == "post" and url.endswith("/_api/cursor"):
                sent += 1
                if sent > limit:
                    raise RuntimeError("cut")
            return real(self, session, method, url, *args, **kwargs)

        monkeypatch.setattr(ObservedHTTPClient, "send_request", cut)
        try:
            merge_short_forms(db, tenant_id=tenant)
            finished = True
        except RuntimeError:
            finished = False
        monkeypatch.setattr(ObservedHTTPClient, "send_request", real)
        merge_short_forms(db, tenant_id=tenant)
        _assert_merged(db, tenant)
        if finished:
            assert allowed > 5  # it was cut at several points before it ran through
            return
    pytest.fail("the pass never ran through")


def test_purging_the_longer_entity_brings_the_short_one_back(db: StandardDatabase) -> None:
    store(db, content="then Ridgeline met Orion.", tenant_id="t", agent_id="a")
    store(db, content="Ridgeline Ventures grew.", tenant_id="t", agent_id="b")
    merge_short_forms(db, tenant_id="t")
    purge(db, tenant_id="t", agent_id="b")
    short = _entity(db, "t", "Ridgeline")
    assert short["invalid_at"] is None and short.get("merged_into") is None


@pytest.mark.parametrize("enabled", [False, True])
def test_dream_state_merges_only_when_enabled(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    assert type(settings).model_fields["merge_short_forms"].default is False
    monkeypatch.setattr(settings, "merge_short_forms", enabled)
    _write(db, "t")
    result = run_dream_state(
        db, tenant_id="t", generator=FakeGenerator(handler=lambda p, s: "DISTINCT"))
    assert result.merged == int(enabled)
    assert (_entity(db, "t", "Ridgeline")["invalid_at"] is not None) is enabled


def test_dream_state_logs_the_merge_count_before_any_model_call(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "merge_short_forms", True)
    log = Mock()
    monkeypatch.setattr(dream, "logger", log)
    _write(db, "t")

    def rejected(prompt: str, system: str | None) -> str:
        raise RuntimeError("model call failed")

    with pytest.raises(RuntimeError):
        run_dream_state(
            db, tenant_id="t", generator=FakeGenerator(handler=rejected), mention_threshold=1)
    log.info.assert_called_once_with("short names merged", extra={"merged": 1})


def _only_merge(db: StandardDatabase, tenant: str = "t", **kwargs: Any) -> dict[str, Any]:
    (merge,) = merge_report(db, tenant_id=tenant, **kwargs)
    return merge


def test_report_shows_each_merge_with_both_sides(db: StandardDatabase) -> None:
    _write(db, "t")
    _write(db, "other")
    assert merge_report(db, tenant_id="t") == []
    merge_short_forms(db, tenant_id="t")
    merge = _only_merge(db)
    assert (merge["name"], merge["into_name"]) == ("Ridgeline", "Ridgeline Ventures")
    assert merge["id"] == _entity(db, "t", "Ridgeline")["_key"]
    assert merge["into_id"] == _entity(db, "t", "Ridgeline Ventures")["_key"]
    assert merge["merged_at"]
    assert sorted(merge["samples"]) == sorted(_TURNS[1:])
    assert merge["into_samples"] == [_TURNS[0]]
    assert merge_report(db, tenant_id="other") == []
    assert len(_only_merge(db, samples=1)["samples"]) == 1


def test_report_includes_later_writes_of_the_short_name(db: StandardDatabase) -> None:
    _write(db, "t")
    merge_short_forms(db, tenant_id="t")
    _write(db, "t", "Ridgeline sold a stake.")
    assert "Ridgeline sold a stake." in _only_merge(db)["samples"]


def test_report_knows_moved_links_the_text_does_not_show(db: StandardDatabase) -> None:
    _write(db, "t", "Ridgeline Ventures backs Orion.", "then RIDGELINE met Orion.")
    merge_short_forms(db, tenant_id="t")
    # stored as "RIDGELINE": the same name, which no text match on it would find
    short = next(db.aql.execute(  # type: ignore[arg-type, call-overload]
        "FOR e IN entities FILTER e.tenant_id == 't' AND e.merged_into != null RETURN e.name"))
    db.aql.execute("FOR e IN entities FILTER e.tenant_id == 't' AND e.merged_into != null "
                   "UPDATE e WITH { name: 'Ridgeline' } IN entities")
    assert short == "RIDGELINE"
    assert _only_merge(db)["samples"] == ["then RIDGELINE met Orion."]


def _forget(db: StandardDatabase, *texts: str) -> None:
    keys = list(db.aql.execute(  # type: ignore[arg-type]
        "FOR m IN memories FILTER m.tenant_id == 't' AND m.text IN @texts RETURN m._key",
        bind_vars={"texts": list(texts)},
    ))
    assert forget_memories(db, tenant_id="t", memory_keys=keys) == len(texts)


def test_report_lists_a_merge_with_no_sample_to_show(db: StandardDatabase) -> None:
    _write(db, "t")
    merge_short_forms(db, tenant_id="t")
    # links as an earlier version moved them, unmarked; what is left names both forms
    db.aql.execute("FOR e IN mentions UPDATE e WITH { merged_short: null } IN mentions")
    _forget(db, *_TURNS[1:])
    merge = _only_merge(db)
    assert merge["samples"] == [] and merge["into_samples"] == [_TURNS[0]]


def test_report_hides_a_merge_whose_name_is_in_no_readable_memory(db: StandardDatabase) -> None:
    _write(db, "t")
    merge_short_forms(db, tenant_id="t")
    _forget(db, *_TURNS)
    assert merge_report(db, tenant_id="t") == []


def test_report_marks_a_merge_whose_longer_name_was_retired(db: StandardDatabase) -> None:
    _write(db, "t")
    merge_short_forms(db, tenant_id="t")
    assert _only_merge(db)["into_retired_at"] is None
    db.collection("entities").update(
        {"_key": _entity(db, "t", "Ridgeline Ventures")["_key"], "invalid_at": "2026-01-02"})
    assert _only_merge(db)["into_retired_at"] == "2026-01-02"


@pytest.mark.parametrize(
    ("allowed", "visible"),
    [(None, True), (["a"], True), (["a*"], True), (["b"], False), (["b*"], False), ([], False)],
)
def test_report_reads_only_allowed_agents(
    db: StandardDatabase, allowed: list[str] | None, visible: bool
) -> None:
    _write(db, "t")
    merge_short_forms(db, tenant_id="t")
    assert bool(merge_report(db, tenant_id="t", agent_ids=allowed)) is visible


def test_report_endpoint_applies_the_keys_agent_binding(
    api: TestClient, db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(db, "t")
    merge_short_forms(db, tenant_id="t")
    monkeypatch.setattr(settings, "api_keys", {
        "k_a": ApiKeyEntry(tenant_id="t", scope="read", agent_ids=["a"]),
        "k_b": ApiKeyEntry(tenant_id="t", scope="read", agent_ids=["b"]),
    })

    def get(key: str, query: str = "tenant_id=t") -> Any:
        return api.get(f"/v1/entities/merges?{query}",
                       headers={"authorization": f"Bearer {key}"})

    (merge,) = get("k_a").json()["merges"]
    assert merge["name"] == "Ridgeline"
    assert get("k_b").json() == {"merges": []}
    assert get("k_a", "tenant_id=other").status_code == 403
    assert get("k_a", "tenant_id=t&limit=201").status_code == 422
    assert api.get("/v1/entities/merges?tenant_id=t").status_code == 401
