"""Graph-algorithmic salience (DESIGN.md §9/§13).

Pregel was removed in ArangoDB 3.12, so centrality is computed in-process with a
short PageRank power-iteration over the tenant's (small) entity subgraph. The
normalized result (`centrality` ∈ [0,1], top entity = 1.0) is written back onto
entities and used as a retrieval-ranking signal + a Graph Explorer cue. The pure
`pagerank` is unit-testable without a database.
"""

from __future__ import annotations

from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

_NODES = """
FOR e IN entities
  FILTER e.tenant_id == @tenant_id AND e.invalid_at == null
  RETURN e._key
"""

_EDGES = """
FOR edge IN relates_to
  LET f = PARSE_IDENTIFIER(edge._from).key
  LET t = PARSE_IDENTIFIER(edge._to).key
  FILTER f IN @keys AND t IN @keys
  RETURN [f, t]
"""

_WRITE = """
FOR e IN entities
  FILTER e.tenant_id == @tenant_id AND e.invalid_at == null
  UPDATE e WITH { centrality: NOT_NULL(@scores[e._key], 0) } IN entities
"""


def pagerank(
    nodes: list[str],
    edges: list[tuple[str, str]],
    *,
    damping: float = 0.85,
    iterations: int = 40,
    tol: float = 1e-6,
) -> dict[str, float]:
    """Undirected PageRank via power iteration → normalized scores (max = 1.0)."""
    n = len(nodes)
    if n == 0:
        return {}
    adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    for a, b in edges:
        if a in adj and b in adj and a != b:
            adj[a].append(b)
            adj[b].append(a)  # relates_to is undirected
    score = {nid: 1.0 / n for nid in nodes}
    teleport = (1.0 - damping) / n
    for _ in range(iterations):
        nxt = {nid: teleport for nid in nodes}
        dangling = damping * sum(score[nid] for nid in nodes if not adj[nid]) / n
        for nid in nodes:
            if not adj[nid]:
                continue
            share = damping * score[nid] / len(adj[nid])
            for nb in adj[nid]:
                nxt[nb] += share
        for nid in nodes:
            nxt[nid] += dangling
        if sum(abs(nxt[nid] - score[nid]) for nid in nodes) < tol:
            score = nxt
            break
        score = nxt
    top = max(score.values()) or 1.0
    return {nid: s / top for nid, s in score.items()}  # normalize so the hub = 1.0


def compute_centrality(db: StandardDatabase, *, tenant_id: str) -> dict[str, int]:
    """Recompute + persist normalized PageRank centrality for a tenant's entities."""
    nodes = list(cast(Cursor, db.aql.execute(_NODES, bind_vars={"tenant_id": tenant_id})))
    if not nodes:
        return {"entities": 0}
    raw_edges = list(
        cast(Cursor, db.aql.execute(_EDGES, bind_vars={"keys": nodes}))
    )
    edges = [(e[0], e[1]) for e in raw_edges]
    scores = pagerank(nodes, edges)
    bind: dict[str, Any] = {"tenant_id": tenant_id, "scores": scores}
    db.aql.execute(_WRITE, bind_vars=bind)
    return {"entities": len(nodes)}
