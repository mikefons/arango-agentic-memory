"""Right to be forgotten / cascade delete (DESIGN.md §17).

`forget` is the immediate, user-facing soft-delete: it sets `invalid_at` across
the subject's memories + entities, which removes them from every retrieval
surface at once (BM25/vector filter memory `invalid_at`; graph filters entity
`invalid_at`). `purge` is the destructive, ops-triggered follow-up: it
hard-deletes the subject's vertices + the edges touching them — episodes
included (the one sanctioned exception to WORM, §17) — and drops the vector
index so retrieval rebuilds it clean (4a/2a self-heal). Both are scoped to a
tenant, optionally narrowed to one agent.
"""

from __future__ import annotations

from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..models import utcnow_iso
from ..schema.collections import EDGE_COLLECTIONS, drop_vector_index

_VERTEX_COLLECTIONS = ("memories", "entities", "episodes", "steps")
_SOFT_DELETABLE = ("memories", "entities")

_SOFT = """
FOR doc IN @@coll
  FILTER doc.tenant_id == @tenant_id AND (@agent_id == null OR doc.agent_id == @agent_id)
  FILTER doc.invalid_at == null
  UPDATE doc WITH { invalid_at: @now } IN @@coll
  RETURN 1
"""

_COLLECT_IDS = """
FOR doc IN @@coll
  FILTER doc.tenant_id == @tenant_id AND (@agent_id == null OR doc.agent_id == @agent_id)
  RETURN doc._id
"""

_REMOVE_EDGES = """
FOR e IN @@coll
  FILTER e._from IN @ids OR e._to IN @ids
  REMOVE e IN @@coll
  RETURN 1
"""

_REMOVE_DOCS = """
FOR doc IN @@coll
  FILTER doc.tenant_id == @tenant_id AND (@agent_id == null OR doc.agent_id == @agent_id)
  REMOVE doc IN @@coll
  RETURN 1
"""


def _run(db: StandardDatabase, query: str, **bind: Any) -> int:
    return len(list(cast(Cursor, db.aql.execute(query, bind_vars=bind))))


def forget(db: StandardDatabase, *, tenant_id: str, agent_id: str | None = None) -> dict[str, int]:
    """Soft-delete: set `invalid_at` on the subject's memories + entities."""
    now = utcnow_iso()
    counts: dict[str, int] = {}
    for coll in _SOFT_DELETABLE:
        counts[coll] = _run(
            db, _SOFT, **{"@coll": coll, "tenant_id": tenant_id, "agent_id": agent_id, "now": now}
        )
    return counts


def purge(db: StandardDatabase, *, tenant_id: str, agent_id: str | None = None) -> dict[str, int]:
    """Physical hard-delete of the subject's vertices + touching edges (ops only)."""
    ids: list[str] = []
    for coll in _VERTEX_COLLECTIONS:
        cursor = cast(
            Cursor,
            db.aql.execute(
                _COLLECT_IDS,
                bind_vars={"@coll": coll, "tenant_id": tenant_id, "agent_id": agent_id},
            ),
        )
        ids.extend(cursor)

    counts: dict[str, int] = {}
    edges = 0
    for coll in EDGE_COLLECTIONS:
        edges += _run(db, _REMOVE_EDGES, **{"@coll": coll, "ids": ids})
    counts["edges"] = edges

    for coll in _VERTEX_COLLECTIONS:
        counts[coll] = _run(
            db, _REMOVE_DOCS, **{"@coll": coll, "tenant_id": tenant_id, "agent_id": agent_id}
        )

    drop_vector_index(db)
    return counts
