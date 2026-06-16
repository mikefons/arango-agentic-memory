"""Tenant memory-graph read for visualization (DESIGN.md §11, §19).

Returns the **semantic graph** for a tenant — entities (nodes) plus `relates_to`
(typed) and `Supersedes` edges — for an interactive explorer. Unlike
`list_entities`, **superseded entities are included** (carrying `invalid_at`) so a
before/after-supersession view is possible. Embeddings are never returned (§17).
"""

from __future__ import annotations

from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

_NODES = """
FOR e IN entities
  FILTER e.tenant_id == @tenant_id
  RETURN { id: e._key, name: e.name, label: e.label, source: e.source,
           mention_count: e.mention_count, belief: e.belief, centrality: e.centrality,
           community: e.community, valid_time: e.valid_time,
           valid_time_explicit: e.valid_time_explicit, needs_review: e.needs_review,
           conflict_with: e.conflict_with, invalid_at: e.invalid_at }
"""

# Edges whose endpoints are both entities of this tenant. `relationship` carries
# the typed label for relates_to ("associated_with" | "caused_by" | …) and
# "supersedes" for the Supersedes collection.
_EDGES = """
FOR edge IN @@coll
  LET f = PARSE_IDENTIFIER(edge._from).key
  LET t = PARSE_IDENTIFIER(edge._to).key
  FILTER f IN @keys AND t IN @keys
  RETURN { source: f, target: t, relationship: edge.relationship, kind: @kind,
           corroboration: edge.corroboration, belief: edge.belief, weight: edge.weight }
"""


def tenant_graph(db: StandardDatabase, *, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
    """The tenant's entities + relates_to/Supersedes edges (embeddings excluded)."""
    nodes = list(cast(Cursor, db.aql.execute(_NODES, bind_vars={"tenant_id": tenant_id})))
    keys = [n["id"] for n in nodes]
    edges: list[dict[str, Any]] = []
    for coll, kind in (("relates_to", "relates_to"), ("Supersedes", "supersedes")):
        edges += list(
            cast(
                Cursor,
                db.aql.execute(_EDGES, bind_vars={"@coll": coll, "keys": keys, "kind": kind}),
            )
        )
    return {"nodes": nodes, "edges": edges}
