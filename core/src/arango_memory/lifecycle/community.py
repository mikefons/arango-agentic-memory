"""Graph community detection (DESIGN.md §9/§13).

Clusters the tenant's entity subgraph into communities with **synchronous label
propagation** (LPA) — in-process Python over the small `relates_to` subgraph, the
same posture as `salience.pagerank` (no Pregel, no new deps, unit-testable). The
result is a dense integer `community` label per entity, persisted back and used to
**scope Dream State review** (consolidation compares entities within a community)
and as a Graph Explorer cue.

LPA is parameter-free (no fixed k) but order-sensitive, so this variant is made
deterministic: nodes are visited in sorted order, label ties break to the smallest
label, and communities are finally relabeled `0..k-1` by descending size — stable
across runs for reproducible tests + diffs.

LPA can lump two dense clusters joined by a thin bridge into one community — an
inherent limitation at this scale. That only ever makes the Dream State gate
*more* permissive (it skips superseding solely across **different** communities),
so a merge degrades to the prior, ungated behavior; it never causes a wrong merge.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

# Same subgraph the salience pass reads (undirected relates_to).
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
  UPDATE e WITH { community: NOT_NULL(@labels[e._key], -1) } IN entities
"""


def label_propagation(
    nodes: list[str],
    edges: list[tuple[str, str]],
    *,
    iterations: int = 20,
) -> dict[str, int]:
    """Deterministic synchronous LPA → dense community ids (largest community = 0).

    Isolated nodes form their own singleton community.
    """
    if not nodes:
        return {}
    adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    for a, b in edges:
        if a in adj and b in adj and a != b:
            adj[a].append(b)
            adj[b].append(a)  # relates_to is undirected

    ordered = sorted(nodes)
    label = {nid: nid for nid in ordered}  # seed each node with its own key
    for _ in range(iterations):
        changed = False
        for nid in ordered:  # async update in a fixed order → deterministic
            neighbors = adj[nid]
            if not neighbors:
                continue
            counts = Counter(label[nb] for nb in neighbors)
            top = max(counts.values())
            best = min(lbl for lbl, c in counts.items() if c == top)  # tie → smallest
            if best != label[nid]:
                label[nid] = best
                changed = True
        if not changed:
            break

    # Relabel raw labels → dense ids ordered by community size (desc), then label.
    groups: dict[str, list[str]] = {}
    for nid, lbl in label.items():
        groups.setdefault(lbl, []).append(nid)
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    remap = {lbl: idx for idx, (lbl, _members) in enumerate(ranked)}
    return {nid: remap[lbl] for nid, lbl in label.items()}


def compute_communities(db: StandardDatabase, *, tenant_id: str) -> dict[str, int]:
    """Recompute + persist LPA community labels for a tenant's entities."""
    nodes = list(cast(Cursor, db.aql.execute(_NODES, bind_vars={"tenant_id": tenant_id})))
    if not nodes:
        return {"entities": 0, "communities": 0}
    raw_edges = list(cast(Cursor, db.aql.execute(_EDGES, bind_vars={"keys": nodes})))
    edges = [(e[0], e[1]) for e in raw_edges]
    labels = label_propagation(nodes, edges)
    bind: dict[str, Any] = {"tenant_id": tenant_id, "labels": labels}
    db.aql.execute(_WRITE, bind_vars=bind)
    return {"entities": len(nodes), "communities": len(set(labels.values()))}
