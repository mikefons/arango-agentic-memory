"""Graph health stats (DESIGN.md §18 gauges, §19 `stats`).

Per-tenant document counts; also emits a `graph` gauge so observers can track
graph growth over time.
"""

from __future__ import annotations

from typing import cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from .telemetry import metrics

_COUNTS = """
RETURN {
  memories: LENGTH(FOR m IN memories FILTER m.tenant_id == @t RETURN 1),
  entities: LENGTH(FOR e IN entities FILTER e.tenant_id == @t RETURN 1),
  episodes: LENGTH(FOR ep IN episodes FILTER ep.tenant_id == @t RETURN 1),
  steps:    LENGTH(FOR s IN steps FILTER s.tenant_id == @t RETURN 1)
}
"""


def stats(db: StandardDatabase, *, tenant_id: str) -> dict[str, int]:
    """Per-tenant counts across the core collections; emits a `graph` gauge."""
    cursor = cast(Cursor, db.aql.execute(_COUNTS, bind_vars={"t": tenant_id}))
    counts = cast("dict[str, int]", next(iter(cursor)))
    metrics.emit(
        "graph",
        tenant_id=tenant_id,
        entity_count=counts["entities"],
        episode_count=counts["episodes"],
    )
    return counts
