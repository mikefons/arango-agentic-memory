"""Working-memory tier — session TTL + SCM 7-item cap (DESIGN.md §5/§14)."""

from __future__ import annotations

from collections.abc import Callable

from arango.database import StandardDatabase

from arango_memory.config import settings
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult


def test_working_memory_has_type_and_ttl(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_wm1", "agent_id": "a", "session_id": "s1"}
    res = store(db, content="the torch is lit", memory_type="working", **ctx)
    mem = db.collection("memories").get(res.memory_ids[0])
    assert mem["type"] == "working"
    assert mem["expires_at"] is not None  # TTL set
    assert mem["session_id"] == "s1"


def test_episodic_memory_has_no_ttl(db: StandardDatabase) -> None:
    res = store(db, content="a durable fact", tenant_id="t_wm2", agent_id="a")
    mem = db.collection("memories").get(res.memory_ids[0])
    assert mem["type"] == "episodic"
    assert mem["expires_at"] is None  # TTL index ignores null


def test_working_memory_extracts_no_entities(db: StandardDatabase) -> None:
    # Ephemeral scratch must not mint durable semantic entities.
    res = store(
        db, content="Acme Corp and Globex Inc", memory_type="working",
        tenant_id="t_wm3", agent_id="a", session_id="s1",
    )
    assert res.entity_ids == []


def test_scm_cap_promotes_oldest_to_episodic(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_wm4", "agent_id": "a", "session_id": "s1"}
    keys = []
    # Constant content → no topic shift (distinct turn_index keeps memories distinct),
    # so this isolates the SCM capacity cap from the GAM topic-shift flush (§13).
    for i in range(settings.working_capacity + 2):  # two over the cap
        res = store(db, content="scratch buffer note", turn_index=i, memory_type="working", **ctx)
        keys.append(res.memory_ids[0])

    types = [db.collection("memories").get(k)["type"] for k in keys]
    # exactly `working_capacity` remain working; the two oldest were promoted
    assert types.count("working") == settings.working_capacity
    assert types[0] == "episodic" and types[1] == "episodic"
    assert all(t == "working" for t in types[2:])
    # promoted memories lose their TTL
    assert db.collection("memories").get(keys[0])["expires_at"] is None


def test_ttl_index_exists(db: StandardDatabase) -> None:
    names = {idx["name"] for idx in db.collection("memories").indexes()}
    assert "idx_working_ttl" in names


def test_working_memory_is_retrievable(
    db: StandardDatabase, wait_for_searchable: Callable[..., RetrieveResult]
) -> None:
    ctx = {"tenant_id": "t_wm5", "agent_id": "a", "session_id": "s1"}
    store(db, content="the password is mistral", memory_type="working", **ctx)
    result = wait_for_searchable(db, query="password", tenant_id="t_wm5", agent_id="a")
    assert any("password is mistral" in h.text for h in result.hits)
