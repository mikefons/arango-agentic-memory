"""Procedural memory — tool traces and reasoning patterns (DESIGN.md §5, §11).

A step is UPSERTed by its natural key (tenant, agent, tool_name, outcome), so a
recurring tool pattern increments `use_count` rather than duplicating — that
increment is the "reuse" signal. `TOUCHED` links a step to the memory that
triggered it; `TRANSITION` sequences one step after another (workflow order).
"""

from __future__ import annotations

import hashlib
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..models import utcnow_iso

_UPSERT_STEP = """
UPSERT { tenant_id: @tenant_id, agent_id: @agent_id, tool_name: @tool_name, outcome: @outcome }
INSERT @doc
UPDATE { use_count: OLD.use_count + 1, last_used_at: @now, arguments: @arguments }
IN steps
RETURN NEW._key
"""

_GET_STEPS = """
FOR s IN steps
  FILTER s.tenant_id == @tenant_id AND s.agent_id == @agent_id
  FILTER @tool_name == null OR s.tool_name == @tool_name
  SORT s.use_count DESC
  LIMIT @limit
  RETURN s
"""


def step_key(tenant_id: str, agent_id: str, tool_name: str, outcome: str) -> str:
    raw = f"{tenant_id}\x1f{agent_id}\x1f{tool_name}\x1f{outcome}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _edge(db: StandardDatabase, collection: str, key: str, from_id: str, to_id: str) -> None:
    db.collection(collection).insert(
        {"_key": key, "_from": from_id, "_to": to_id, "ingestion_time": utcnow_iso()},
        overwrite_mode="ignore",
        silent=True,
    )


def record_step(
    db: StandardDatabase,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    outcome: str,
    tenant_id: str,
    agent_id: str,
    pattern_summary: str = "",
    source_memory_key: str | None = None,
    prev_step_key: str | None = None,
) -> str:
    """Persist a tool trace; reuse bumps use_count. Returns the step key."""
    now = utcnow_iso()
    key = step_key(tenant_id, agent_id, tool_name, outcome)
    doc: dict[str, Any] = {
        "_key": key,
        "tool_name": tool_name,
        "arguments": arguments,
        "outcome": outcome,
        "pattern_summary": pattern_summary,
        "use_count": 1,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "created_at": now,
        "last_used_at": now,
        "schema_version": "0.1.0",
    }
    bind_vars: dict[str, Any] = {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "tool_name": tool_name,
        "outcome": outcome,
        "doc": doc,
        "arguments": arguments,
        "now": now,
    }
    db.aql.execute(_UPSERT_STEP, bind_vars=bind_vars)

    if source_memory_key:
        _edge(db, "TOUCHED", f"{key}__{source_memory_key}",
              f"steps/{key}", f"memories/{source_memory_key}")
    if prev_step_key and prev_step_key != key:
        _edge(db, "TRANSITION", f"{prev_step_key}__{key}",
              f"steps/{prev_step_key}", f"steps/{key}")
    return key


def get_steps(
    db: StandardDatabase,
    *,
    tenant_id: str,
    agent_id: str,
    tool_name: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Look up procedural memory (tenant/agent-scoped), most-reused first."""
    bind_vars: dict[str, Any] = {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "tool_name": tool_name,
        "limit": limit,
    }
    cursor = cast(Cursor, db.aql.execute(_GET_STEPS, bind_vars=bind_vars))
    return list(cursor)
