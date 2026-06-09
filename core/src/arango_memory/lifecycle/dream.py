"""Consolidation / Dream State (DESIGN.md §13).

A threshold-driven pass (the GAM session-topic trigger is deferred). It reviews
entities that are flagged (`needs_review`) or well-attested (`mention_count ≥
threshold`):
  - **Conflict confirmation** — a flagged entity is reviewed against its
    `conflict_with` target; a confirmed contradiction is superseded (§12),
    a false alarm just clears the flag.
  - **Distillation** — a well-attested entity gets a one-sentence summary from
    the memories that mention it, and is marked consolidated.

Two phases: decide everything first, then a **circuit breaker** halts the whole
run (applies nothing) if too large a fraction would be deprecated — a poisoning
safeguard. It's a callable pass (tested directly); scheduling is an ops concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..generation import Generator, get_generator
from ..models import utcnow_iso
from .conflict import supersede

_CONFLICT_SYSTEM = (
    "Two entities were flagged as similar. Decide if they are the SAME real-world "
    "entity (a duplicate/contradiction) or genuinely DISTINCT. "
    "Reply with exactly CONTRADICTS or DISTINCT."
)
_DISTILL_SYSTEM = (
    "Summarize what is known about this entity in one concise sentence, based only "
    "on the provided memories. No preamble."
)

_CANDIDATES = """
FOR e IN entities
  FILTER e.tenant_id == @tenant_id AND e.invalid_at == null
  FILTER e.needs_review == true OR e.mention_count >= @threshold
  RETURN e
"""

_MENTIONING = """
FOR m IN 1..1 INBOUND @entity_id mentions
  FILTER m.invalid_at == null
  RETURN m.text
"""


@dataclass
class DreamResult:
    reviewed: int = 0
    superseded: int = 0
    consolidated: int = 0
    cleared: int = 0
    breaker_tripped: bool = False


def run_dream_state(
    db: StandardDatabase,
    *,
    tenant_id: str,
    generator: Generator | None = None,
    mention_threshold: int | None = None,
    breaker_threshold: float | None = None,
) -> DreamResult:
    """Review flagged / well-attested entities for one tenant. Returns a summary."""
    gen = generator or get_generator()
    threshold = (
        mention_threshold
        if mention_threshold is not None
        else settings.consolidation_mention_threshold
    )
    breaker = (
        breaker_threshold
        if breaker_threshold is not None
        else settings.dream_breaker_threshold
    )

    bind: dict[str, Any] = {"tenant_id": tenant_id, "threshold": threshold}
    candidates = list(cast(Cursor, db.aql.execute(_CANDIDATES, bind_vars=bind)))
    if not candidates:
        return DreamResult()

    supersessions: list[tuple[str, str]] = []  # (new_key, old_key)
    clears: list[str] = []
    summaries: list[tuple[str, str]] = []

    for entity in candidates:
        key = entity["_key"]
        if entity.get("needs_review") and entity.get("conflict_with"):
            target = cast(
                "dict[str, Any] | None", db.collection("entities").get(entity["conflict_with"])
            )
            if target is None or target.get("invalid_at") is not None:
                clears.append(key)  # nothing left to resolve against
            else:
                verdict = gen.complete(
                    f"A: {entity['name']}\nB: {target['name']}", system=_CONFLICT_SYSTEM
                ).strip().upper()
                if verdict.startswith("CONTRADICTS"):
                    supersessions.append((key, target["_key"]))
                else:
                    clears.append(key)

        if entity.get("mention_count", 0) >= threshold:
            texts = list(
                cast(Cursor, db.aql.execute(_MENTIONING, bind_vars={"entity_id": entity["_id"]}))
            )
            summary = gen.complete(
                "\n".join(f"- {t}" for t in texts), system=_DISTILL_SYSTEM
            ).strip()
            if summary:
                summaries.append((key, summary))

    reviewed = len(candidates)
    if reviewed and len(supersessions) / reviewed > breaker:
        return DreamResult(reviewed=reviewed, breaker_tripped=True)

    now = utcnow_iso()
    entities = db.collection("entities")
    for new_key, old_key in supersessions:
        supersede(db, new_key=new_key, old_key=old_key)
        entities.update({"_key": new_key, "needs_review": False})
    for key in clears:
        entities.update({"_key": key, "needs_review": False})
    for key, summary in summaries:
        entities.update({"_key": key, "summary": summary, "consolidated_at": now})

    return DreamResult(
        reviewed=reviewed,
        superseded=len(supersessions),
        consolidated=len(summaries),
        cleared=len(clears),
    )
