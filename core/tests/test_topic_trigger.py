"""GAM semantic-boundary trigger — session topic-shift detection (DESIGN.md §13)."""

from __future__ import annotations

from typing import Any

from arango.database import StandardDatabase

from arango_memory.ingest.store import store

_A = "alpha beta gamma delta epsilon"          # topic A
_A2 = "alpha beta gamma delta omega"           # close to A (shared tokens)
_B = "zulu yankee xray whiskey victor"         # disjoint from A → topic shift


def _session(db: StandardDatabase, tenant: str, agent: str, sid: str) -> dict[str, Any]:
    return next(
        db.aql.execute(
            "FOR s IN sessions FILTER s.tenant_id == @t AND s.agent_id == @a "
            "AND s.session_id == @s RETURN s",
            bind_vars={"t": tenant, "a": agent, "s": sid},
        )
    )


def test_first_turn_seeds_session_without_shift(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_gam1", "agent_id": "a", "session_id": "s1"}
    store(db, content=_A, turn_index=0, **ctx)
    s = _session(db, "t_gam1", "a", "s1")
    assert s["topic_embedding"] and s["consolidation_due"] is False


def test_same_topic_does_not_shift(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_gam2", "agent_id": "a", "session_id": "s1"}
    store(db, content=_A, turn_index=0, **ctx)
    store(db, content=_A2, turn_index=1, **ctx)  # shares most tokens → cosine ≥ 0.7
    assert _session(db, "t_gam2", "a", "s1")["consolidation_due"] is False


def test_topic_shift_flags_consolidation_due(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_gam3", "agent_id": "a", "session_id": "s1"}
    store(db, content=_A, turn_index=0, **ctx)
    store(db, content=_B, turn_index=1, **ctx)  # disjoint tokens → topic shift
    assert _session(db, "t_gam3", "a", "s1")["consolidation_due"] is True


def test_topic_shift_flushes_working_buffer(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_gam4", "agent_id": "a", "session_id": "s1"}
    working = store(db, content=_A, turn_index=0, memory_type="working", **ctx).memory_ids[0]
    store(db, content=_B, turn_index=1, **ctx)  # shift → flush the working buffer

    mem = db.collection("memories").get(working)
    assert mem["type"] == "episodic" and mem["expires_at"] is None


def test_no_session_id_skips_topic_tracking(db: StandardDatabase) -> None:
    store(db, content=_A, tenant_id="t_gam5", agent_id="a")  # no session_id
    assert db.collection("sessions").count() == 0 or all(
        s["session_id"] != "s1" for s in db.collection("sessions").all()
    )


def test_idempotent_replay_does_not_reshift(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_gam6", "agent_id": "a", "session_id": "s1"}
    store(db, content=_A, turn_index=0, **ctx)
    store(db, content=_A, turn_index=0, **ctx)  # identical → idempotent, is_new False
    sessions = [
        s for s in db.collection("sessions").all()
        if s["tenant_id"] == "t_gam6" and s["session_id"] == "s1"
    ]
    assert len(sessions) == 1 and sessions[0]["consolidation_due"] is False
