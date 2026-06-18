"""GAM semantic-boundary trigger — session topic-shift detection (DESIGN.md §13)."""

from __future__ import annotations

from typing import Any

from arango.database import StandardDatabase

from arango_memory.ingest.store import store

from .conftest import StubEmbedder

_A = "topic A turn one"
_A2 = "topic A turn two"   # near A in embedding space → no shift
_B = "topic B turn"        # orthogonal to A → topic shift

# Explicit geometry (not FakeEmbedder token overlap): cos(A,A2)=0.8 ≥ 0.7 (no shift),
# cos(A,B)=0 < 0.7 (shift). Decouples the threshold band from the default embedder.
_EMB = StubEmbedder({_A: [1.0, 0.0, 0.0], _A2: [0.8, 0.6, 0.0], _B: [0.0, 1.0, 0.0]})


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
    store(db, content=_A, turn_index=0, embedder=_EMB, **ctx)
    s = _session(db, "t_gam1", "a", "s1")
    assert s["topic_embedding"] and s["consolidation_due"] is False


def test_same_topic_does_not_shift(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_gam2", "agent_id": "a", "session_id": "s1"}
    store(db, content=_A, turn_index=0, embedder=_EMB, **ctx)
    store(db, content=_A2, turn_index=1, embedder=_EMB, **ctx)  # cos 0.8 ≥ 0.7 → no shift
    assert _session(db, "t_gam2", "a", "s1")["consolidation_due"] is False


def test_topic_shift_flags_consolidation_due(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_gam3", "agent_id": "a", "session_id": "s1"}
    store(db, content=_A, turn_index=0, embedder=_EMB, **ctx)
    store(db, content=_B, turn_index=1, embedder=_EMB, **ctx)  # cos 0 < 0.7 → topic shift
    assert _session(db, "t_gam3", "a", "s1")["consolidation_due"] is True


def test_topic_shift_flushes_working_buffer(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_gam4", "agent_id": "a", "session_id": "s1"}
    res = store(db, content=_A, turn_index=0, memory_type="working", embedder=_EMB, **ctx)
    working = res.memory_ids[0]
    store(db, content=_B, turn_index=1, embedder=_EMB, **ctx)  # shift → flush the working buffer

    mem = db.collection("memories").get(working)
    assert mem["type"] == "episodic" and mem["expires_at"] is None


def test_no_session_id_skips_topic_tracking(db: StandardDatabase) -> None:
    store(db, content=_A, tenant_id="t_gam5", agent_id="a", embedder=_EMB)  # no session_id
    assert db.collection("sessions").count() == 0 or all(
        s["session_id"] != "s1" for s in db.collection("sessions").all()
    )


def test_idempotent_replay_does_not_reshift(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_gam6", "agent_id": "a", "session_id": "s1"}
    store(db, content=_A, turn_index=0, embedder=_EMB, **ctx)
    store(db, content=_A, turn_index=0, embedder=_EMB, **ctx)  # identical → idempotent
    sessions = [
        s for s in db.collection("sessions").all()
        if s["tenant_id"] == "t_gam6" and s["session_id"] == "s1"
    ]
    assert len(sessions) == 1 and sessions[0]["consolidation_due"] is False
