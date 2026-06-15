"""Semantic-entity query + cold-start seed (DESIGN.md §11, §19).

`get_entity` / `list_entities` are reads (the ingestion write path lives in
`ingest/entities.py`); `seed` pre-populates semantic memory from a profile.
All projections **exclude embeddings** (§17 inversion defense).
"""

from __future__ import annotations

from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from .embedding import Embedder, get_embedder
from .models import utcnow_iso

# Public projection — never exposes embeddings (§17).
_PROJECT = """{ id: e._key, name: e.name, label: e.label, summary: e.summary,
                mention_count: e.mention_count, confidence: e.confidence,
                belief: e.belief, centrality: e.centrality, community: e.community,
                source: e.source,
                needs_review: e.needs_review, conflict_with: e.conflict_with }"""

_GET = f"""
FOR e IN entities
  FILTER e._key == @key AND e.tenant_id == @tenant_id AND e.invalid_at == null
  LIMIT 1
  RETURN {_PROJECT}
"""

_RELATED = """
FOR v, edge IN 1..1 ANY @entity_id relates_to
  FILTER v.invalid_at == null
  RETURN DISTINCT { id: v._key, name: v.name, label: v.label, relationship: edge.relationship }
"""

_LIST = f"""
FOR e IN entities
  FILTER e.tenant_id == @tenant_id AND e.invalid_at == null
  FILTER @agent_id == null OR e.agent_id == @agent_id
  FILTER @label == null OR e.label == @label
  SORT e.mention_count DESC
  LIMIT @limit
  RETURN {_PROJECT}
"""

_SEED_UPSERT = """
UPSERT { tenant_id: @tenant_id, name: @name, label: @label }
INSERT @doc
UPDATE {} IN entities
RETURN NEW._key
"""


def get_entity(db: StandardDatabase, *, entity_id: str, tenant_id: str) -> dict[str, Any] | None:
    """Fetch one entity (by id) + its relates_to neighbours. None if absent/forgotten."""
    cursor = cast(
        Cursor, db.aql.execute(_GET, bind_vars={"key": entity_id, "tenant_id": tenant_id})
    )
    rows = list(cursor)
    if not rows:
        return None
    related = list(
        cast(Cursor, db.aql.execute(_RELATED, bind_vars={"entity_id": f"entities/{entity_id}"}))
    )
    return {"entity": rows[0], "related": related}


def list_entities(
    db: StandardDatabase,
    *,
    tenant_id: str,
    agent_id: str | None = None,
    label: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List a tenant's semantic entities (optionally filtered by agent/label)."""
    bind: dict[str, Any] = {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "label": label,
        "limit": limit,
    }
    return list(cast(Cursor, db.aql.execute(_LIST, bind_vars=bind)))


def _profile_items(profile: dict[str, Any]) -> list[str]:
    items: list[str] = []
    for field in ("role", "domain"):
        value = profile.get(field)
        if isinstance(value, str) and value.strip():
            items.append(value.strip())
    for pref in profile.get("preferences") or []:
        if isinstance(pref, str) and pref.strip():
            items.append(pref.strip())
    return list(dict.fromkeys(items))


def seed(
    db: StandardDatabase,
    *,
    profile: dict[str, Any],
    tenant_id: str,
    agent_id: str,
    embedder: Embedder | None = None,
) -> list[str]:
    """Cold-start seed: each profile item → a seed entity (source=seed, confidence=0.6).

    Idempotent UPSERT that never clobbers an existing entity, so observed facts
    (confidence 1.0) win over seeds (DESIGN.md §11).
    """
    emb = embedder or get_embedder()
    now = utcnow_iso()
    keys: list[str] = []
    for name in _profile_items(profile):
        doc: dict[str, Any] = {
            "name": name,
            "label": "Concept",
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "embedding": emb.embed(name),
            "embedding_model": emb.model,
            "embedding_version": emb.version,
            "mention_count": 1,
            "confidence": 0.6,
            "source": "seed",
            "summary": "",
            "consolidated_at": None,
            "ingestion_time": now,
            "valid_time": now,
            "valid_time_explicit": False,
            "invalid_at": None,
            "created_at": now,
            "accessed_at": now,
            "needs_review": False,
            "conflict_with": None,
            "schema_version": "0.1.0",
        }
        cursor = cast(
            Cursor,
            db.aql.execute(
                _SEED_UPSERT,
                bind_vars={"tenant_id": tenant_id, "name": name, "label": "Concept", "doc": doc},
            ),
        )
        keys.append(cast(str, next(iter(cursor))))
    return list(dict.fromkeys(keys))
