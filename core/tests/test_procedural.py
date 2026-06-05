"""Integration tests for procedural memory (DESIGN.md §5, §11)."""

from __future__ import annotations

from arango.database import StandardDatabase

from arango_memory.ingest.procedural import get_steps, record_step
from arango_memory.ingest.store import store


def test_record_step_writes_step_and_edges(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_p", "agent_id": "a"}
    memory_key = store(db, content="trigger message", **ctx).memory_ids[0]

    k1 = record_step(
        db, tool_name="search", arguments={"q": "x"}, outcome="success",
        pattern_summary="searched the web", source_memory_key=memory_key, **ctx,
    )
    assert db.collection("steps").count() == 1
    assert db.collection("TOUCHED").count() == 1  # step → triggering memory

    record_step(db, tool_name="fetch", arguments={"u": "y"}, outcome="success",
                prev_step_key=k1, **ctx)
    assert db.collection("steps").count() == 2
    assert db.collection("TRANSITION").count() == 1  # step → step


def test_reuse_increments_use_count_and_updates_args(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_pu", "agent_id": "a"}
    record_step(db, tool_name="search", arguments={"q": "first"}, outcome="success", **ctx)
    record_step(db, tool_name="search", arguments={"q": "second"}, outcome="success", **ctx)

    steps = get_steps(db, **ctx)
    assert len(steps) == 1                       # same natural key → one step
    assert steps[0]["use_count"] == 2
    assert steps[0]["arguments"] == {"q": "second"}


def test_outcome_distinguishes_steps(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "t_po", "agent_id": "a"}
    record_step(db, tool_name="search", arguments={}, outcome="success", **ctx)
    record_step(db, tool_name="search", arguments={}, outcome="failure", **ctx)
    assert len(get_steps(db, **ctx)) == 2        # success vs failure are distinct


def test_get_steps_is_tenant_scoped_and_filterable(db: StandardDatabase) -> None:
    record_step(db, tool_name="search", arguments={}, outcome="success",
                tenant_id="t_a", agent_id="a")
    record_step(db, tool_name="fetch", arguments={}, outcome="success",
                tenant_id="t_a", agent_id="a")
    record_step(db, tool_name="search", arguments={}, outcome="success",
                tenant_id="t_b", agent_id="a")

    assert len(get_steps(db, tenant_id="t_a", agent_id="a")) == 2
    assert len(get_steps(db, tenant_id="t_a", agent_id="a", tool_name="search")) == 1
    assert len(get_steps(db, tenant_id="t_b", agent_id="a")) == 1
