"""Memory-level fact supersession (GX-2, DESIGN.md §12).

Graphiti closes a fact's validity window when a newer fact contradicts it. Our unit is the
memory: a Dream State pass compares each not-yet-checked memory with the older memories of the
same agent that share an entity with it and read alike (`SUPERSESSION_GATE=entity`) — or, with
`SUPERSESSION_GATE=similarity`, the nearest ones by embedding alone — and asks the LLM which of
them it updates. An updated memory gets `superseded_by` (the newer memory's key) and `valid_to` (the
newer memory's time). Nothing is hidden — `invalid_at` stays null — because RQ-3 showed
temporal reasoning needs the old statements; retrieval uses the link only to rank the current
statement above a stale one it retrieved, and to annotate the stale line.

Incremental: each processed memory is stamped `supersession_checked_at`, so a later pass only
looks at new memories. Memories are processed oldest-first, so a chain (Boston → Chicago →
Denver) links each statement to the next rather than all to the newest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..generation import Generator, get_generator
from ..models import utcnow_iso
from ..retrieve.search import memory_time_key
from ..telemetry import metrics

_SUPERSEDE_SYSTEM = (
    "You compare a NEW statement with OLDER statements from the same history. An older "
    "statement is SUPERSEDED when the new one gives a newer value for the same fact (a change, "
    "update or correction), so the older one is no longer current. Statements that only "
    "relate, add detail, or concern different facts are not superseded. Reply with the numbers "
    "of the superseded older statements, comma-separated, or NONE."
)

# Each statement is clipped before it reaches the prompt: long chat turns would otherwise make
# one comparison cost as much as a whole distillation.
_MAX_CHARS = 1500

_UNCHECKED = """
FOR m IN memories
  FILTER m.tenant_id == @tenant_id AND m.invalid_at == null AND m.type != "working"
     AND m.supersession_checked_at == null
  RETURN { key: m._key, text: m.text, agent_id: m.agent_id, embedding: m.embedding,
           event_time: m.event_time, created_at: m.created_at }
"""

# Older-statement candidates: same tenant + agent, sharing an entity, still current, and close
# in embedding space. The per-entity LIMIT bounds a hub entity's fan-out (cf. SC-1d).
_CANDIDATES = """
LET ents = (FOR e IN 1..1 OUTBOUND CONCAT('memories/', @key) mentions
              FILTER e.invalid_at == null RETURN e._id)
FOR eid IN ents
  FOR o IN 1..1 INBOUND eid mentions
    FILTER o._key != @key AND o.tenant_id == @tenant_id AND o.agent_id == @agent_id
       AND o.invalid_at == null AND o.superseded_by == null AND o.type != "working"
    LIMIT @per_entity
    COLLECT okey = o._key
    LET doc = DOCUMENT("memories", okey)
    LET sim = COSINE_SIMILARITY(doc.embedding, @vec)
    FILTER sim >= @min_sim
    SORT sim DESC
    LIMIT @limit
    RETURN { key: okey, text: doc.text, event_time: doc.event_time,
             created_at: doc.created_at }
"""
_PER_ENTITY = 200
_FETCH = 20

# Similarity gate: the nearest memories of the same agent by embedding alone, no shared entity
# required. Exact scan over the agent's memories (the scope index narrows it to the tenant).
_SIMILAR = """
FOR o IN memories
  FILTER o.tenant_id == @tenant_id AND o.agent_id == @agent_id AND o.invalid_at == null
     AND o._key != @key AND o.superseded_by == null AND o.type != "working"
  LET sim = COSINE_SIMILARITY(o.embedding, @vec)
  FILTER sim >= @min_sim
  SORT sim DESC
  LIMIT @limit
  RETURN { key: o._key, text: o.text, event_time: o.event_time, created_at: o.created_at }
"""
# The scan can't tell older from newer (times are parsed in Python), so fetch deeper.
_SIMILAR_FETCH = 50


@dataclass
class SupersessionResult:
    checked: int = 0
    compared: int = 0  # LLM comparisons made
    superseded: int = 0


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_CHARS else text[:_MAX_CHARS] + "…"


def _parse(reply: str, n: int) -> list[int]:
    """1-based indices from the LLM reply, kept only when in range; NONE → []."""
    return sorted({i for i in map(int, re.findall(r"\d+", reply)) if 1 <= i <= n})


def run_supersession(
    db: StandardDatabase, *, tenant_id: str, generator: Generator | None = None
) -> SupersessionResult:
    """Link each new memory to the older statements it updates. Returns counts."""
    gen = generator or get_generator()
    rows = list(cast(Cursor, db.aql.execute(_UNCHECKED, bind_vars={"tenant_id": tenant_id})))
    floor = datetime.min

    def when(row: dict[str, Any]) -> datetime:
        return memory_time_key(row.get("event_time"), row.get("created_at")) or floor

    rows.sort(key=when)
    memories = db.collection("memories")
    result = SupersessionResult()
    for row in rows:
        now = utcnow_iso()
        mine = when(row)
        older: list[dict[str, Any]] = []
        if row.get("embedding"):
            bind: dict[str, Any] = {
                "key": row["key"], "tenant_id": tenant_id, "agent_id": row["agent_id"],
                "vec": row["embedding"], "min_sim": settings.supersession_min_similarity,
            }
            if settings.supersession_gate == "similarity":
                query, bind["limit"] = _SIMILAR, _SIMILAR_FETCH
            else:
                query, bind["limit"], bind["per_entity"] = _CANDIDATES, _FETCH, _PER_ENTITY
            found = cast(Cursor, db.aql.execute(query, bind_vars=bind))
            older = [o for o in found if when(o) < mine][: settings.supersession_max_candidates]
        if older:
            prompt = f"NEW: {_clip(row['text'])}\nOLDER:\n" + "\n".join(
                f"{i}. {_clip(o['text'])}" for i, o in enumerate(older, 1)
            )
            reply = gen.complete(prompt, system=_SUPERSEDE_SYSTEM, max_tokens=32)
            result.compared += 1
            valid_to = row.get("event_time") or row.get("created_at")
            for i in _parse(reply, len(older)):
                memories.update({"_key": older[i - 1]["key"], "superseded_by": row["key"],
                                 "valid_to": valid_to, "superseded_at": now})
                result.superseded += 1
        memories.update({"_key": row["key"], "supersession_checked_at": now})
        result.checked += 1

    metrics.emit("supersession", checked=result.checked, compared=result.compared,
                 superseded=result.superseded)
    return result
