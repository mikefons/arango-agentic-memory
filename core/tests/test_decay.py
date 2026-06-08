"""Decay + spaced repetition + soft-deprecation sweep (DESIGN.md §11)."""

from __future__ import annotations

import math
from collections.abc import Callable

from arango.database import StandardDatabase

from arango_memory.ingest.store import store
from arango_memory.lifecycle.decay import decay_sweep, effective_strength
from arango_memory.models import utcnow_iso
from arango_memory.retrieve.search import RetrieveResult, retrieve


def _set_accessed(db: StandardDatabase, memory_key: str, iso: str) -> None:
    db.collection("memories").update({"_key": memory_key, "accessed_at": iso})


def _days_ago(days: float) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


# ── unit ──────────────────────────────────────────────────
def test_effective_strength_decays_with_age() -> None:
    now = utcnow_iso()
    fresh = effective_strength(1.0, now, now, 0.02)
    stale = effective_strength(1.0, _days_ago(100), now, 0.02)
    assert math.isclose(fresh, 1.0, abs_tol=1e-6)
    assert stale < fresh
    assert math.isclose(stale, math.exp(-0.02 * 100), rel_tol=1e-3)


# ── retrieval ranking ─────────────────────────────────────
def test_recent_memory_outranks_stale_on_same_query(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_decay", "agent_id": "a"}
    fresh = store(db, content="alpha signal recent", turn_index=0, **ctx).memory_ids[0]
    stale = store(db, content="alpha signal old", turn_index=1, **ctx).memory_ids[0]
    _set_accessed(db, stale, _days_ago(200))
    wait_for_searchable(db, query="alpha signal", **ctx)

    result = retrieve(db, query="alpha signal", k=10, **ctx)
    texts = [h.text for h in result.hits]
    assert texts[0] == "alpha signal recent"            # fresh ranks first
    assert texts.index("alpha signal recent") < texts.index("alpha signal old")
    # `fresh` was just retrieved → its accessed_at refreshed (spaced repetition).
    assert db.collection("memories").get(fresh)["access_count"] >= 2


def test_decay_sweep_soft_deprecates_stale_memories(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_sweep", "agent_id": "a"}
    keep = store(db, content="bravo keep", turn_index=0, **ctx).memory_ids[0]
    drop = store(db, content="bravo drop", turn_index=1, **ctx).memory_ids[0]
    _set_accessed(db, drop, _days_ago(500))  # well past the floor

    n = decay_sweep(db, lambda_per_day=0.02, floor=0.1)
    assert n == 1
    assert db.collection("memories").get(drop)["invalid_at"] is not None
    assert db.collection("memories").get(keep)["invalid_at"] is None

    # Soft-deprecated memory drops out of retrieval; the fresh one remains.
    result = wait_for_searchable(db, query="bravo", **ctx)
    texts = [h.text for h in result.hits]
    assert "bravo keep" in texts
    assert "bravo drop" not in texts
