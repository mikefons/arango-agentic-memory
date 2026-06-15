"""Ontology evolution (DESIGN.md §13, v2 research — flag-gated).

The extractor falls back to a generic `associated_with` edge whenever it sees two
entities co-occur without a typed relationship. Over time, recurring co-occurrence
between the *same kinds* of entities (e.g. many `Person`—`Company` pairs) hints at
a real relationship the ontology is missing.

This pass groups `associated_with` edges by their endpoint **label-pair**, asks the
generator to name the relationship the cluster represents, and records a **proposal**
— it never mutates the graph on its own. A human approves (or rejects) via the API;
approval relabels the tenant's `associated_with` edges for that label-pair to the
proposed relationship (a scoped data migration — edge "types" are attribute values,
not collections). Keyless by default: the Fake generator proposes nothing.
"""

from __future__ import annotations

import re
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..generation import Generator, get_generator
from ..models import utcnow_iso

_PROPOSALS = "ontology_proposals"

_SYSTEM = (
    "Many entity pairs of two given types keep co-occurring. Propose a single concise "
    "snake_case relationship label (a short verb phrase, read as A→B, e.g. works_at, "
    "located_in, founded_by) that best names how they typically relate. If there is no "
    "consistent relationship, reply with exactly NONE. Reply with only the label."
)

# Co-occurrence edges grouped by sorted endpoint label-pair, with example name pairs.
_CLUSTERS = """
FOR edge IN relates_to
  FILTER edge.relationship == "associated_with"
  LET f = DOCUMENT(edge._from)
  LET t = DOCUMENT(edge._to)
  FILTER f != null AND t != null
     AND f.tenant_id == @tenant_id AND f.invalid_at == null AND t.invalid_at == null
  LET labels = f.label <= t.label ? [f.label, t.label] : [t.label, f.label]
  COLLECT la = labels[0], lb = labels[1] INTO rows = { a: f.name, b: t.name }
  FILTER LENGTH(rows) >= @min_support
  SORT LENGTH(rows) DESC
  RETURN { label_a: la, label_b: lb, support: LENGTH(rows), examples: SLICE(rows, 0, 8) }
"""

_UPSERT = """
UPSERT { _key: @key }
INSERT @doc
UPDATE {
  proposed_relationship: @doc.proposed_relationship,
  support: @doc.support,
  examples: @doc.examples,
  updated_at: @doc.updated_at
}
IN ontology_proposals
RETURN NEW._key
"""

_LIST = """
FOR p IN ontology_proposals
  FILTER p.tenant_id == @tenant_id
     AND (@status == null OR p.status == @status)
  SORT p.support DESC
  RETURN UNSET(p, "_rev", "_id")
"""

# Relabel a tenant's associated_with edges for one label-pair to the approved type.
_RELABEL = """
FOR edge IN relates_to
  FILTER edge.relationship == "associated_with"
  LET f = DOCUMENT(edge._from)
  LET t = DOCUMENT(edge._to)
  FILTER f != null AND t != null AND f.tenant_id == @tenant_id
  LET labels = f.label <= t.label ? [f.label, t.label] : [t.label, f.label]
  FILTER labels[0] == @label_a AND labels[1] == @label_b
  UPDATE edge WITH { relationship: @rel } IN relates_to
  COLLECT WITH COUNT INTO n
  RETURN n
"""


def _normalize(label: str) -> str:
    """LLM text → a snake_case relationship label (or "" to reject)."""
    cleaned = label.strip().splitlines()[0].strip() if label.strip() else ""
    if not cleaned or cleaned.upper() == "NONE":
        return ""
    slug = re.sub(r"[^a-z0-9]+", "_", cleaned.lower()).strip("_")
    return slug if slug and slug != "associated_with" else ""


def _proposal_key(tenant_id: str, label_a: str, label_b: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", f"{tenant_id}__{label_a}__{label_b}")


def propose_relationship_types(
    db: StandardDatabase,
    *,
    tenant_id: str,
    generator: Generator | None = None,
    min_support: int | None = None,
) -> dict[str, int]:
    """Scan associated_with clusters → upsert pending relationship proposals."""
    gen = generator or get_generator()
    support = min_support if min_support is not None else settings.ontology_min_support
    cluster_bind: dict[str, Any] = {"tenant_id": tenant_id, "min_support": support}
    clusters = list(cast(Cursor, db.aql.execute(_CLUSTERS, bind_vars=cluster_bind)))
    now = utcnow_iso()
    proposed = 0
    for cluster in clusters:
        examples = "\n".join(f"- {e['a']} — {e['b']}" for e in cluster["examples"])
        prompt = (
            f"Type A: {cluster['label_a']}\nType B: {cluster['label_b']}\n"
            f"Examples:\n{examples}"
        )
        relationship = _normalize(gen.complete(prompt, system=_SYSTEM))
        if not relationship:
            continue
        key = _proposal_key(tenant_id, cluster["label_a"], cluster["label_b"])
        doc: dict[str, Any] = {
            "_key": key,
            "tenant_id": tenant_id,
            "label_a": cluster["label_a"],
            "label_b": cluster["label_b"],
            "proposed_relationship": relationship,
            "support": cluster["support"],
            "examples": cluster["examples"],
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }
        db.aql.execute(_UPSERT, bind_vars={"key": key, "doc": doc})
        proposed += 1
    return {"clusters": len(clusters), "proposed": proposed}


def list_proposals(
    db: StandardDatabase, *, tenant_id: str, status: str | None = None
) -> list[dict[str, Any]]:
    return list(
        cast(Cursor, db.aql.execute(_LIST, bind_vars={"tenant_id": tenant_id, "status": status}))
    )


def approve_proposal(db: StandardDatabase, *, tenant_id: str, key: str) -> dict[str, Any]:
    """Approve a pending proposal: relabel the tenant's matching co-occurrence edges."""
    proposals = db.collection(_PROPOSALS)
    proposal = cast("dict[str, Any] | None", proposals.get(key))
    if proposal is None or proposal.get("tenant_id") != tenant_id:
        return {"status": "not_found", "relabeled": 0}
    rel = proposal["proposed_relationship"]
    bind: dict[str, Any] = {
        "tenant_id": tenant_id,
        "label_a": proposal["label_a"],
        "label_b": proposal["label_b"],
        "rel": rel,
    }
    relabeled = next(iter(cast(Cursor, db.aql.execute(_RELABEL, bind_vars=bind))), 0)
    proposals.update({"_key": key, "status": "approved", "updated_at": utcnow_iso()})
    return {"status": "approved", "relationship": rel, "relabeled": relabeled}


def reject_proposal(db: StandardDatabase, *, tenant_id: str, key: str) -> dict[str, Any]:
    """Reject a proposal: mark it rejected; the graph is untouched."""
    proposals = db.collection(_PROPOSALS)
    proposal = cast("dict[str, Any] | None", proposals.get(key))
    if proposal is None or proposal.get("tenant_id") != tenant_id:
        return {"status": "not_found"}
    proposals.update({"_key": key, "status": "rejected", "updated_at": utcnow_iso()})
    return {"status": "rejected"}
