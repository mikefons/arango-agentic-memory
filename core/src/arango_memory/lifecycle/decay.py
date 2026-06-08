"""Episodic memory decay (DESIGN.md §11).

Decay is applied two ways, per the rev-4 decision:
  - **Lazily** at retrieval — `effective_strength` is a ranking multiplier, so
    ordering is always fresh without a batch job (see `retrieve.search`).
  - **A scheduled sweep** — `decay_sweep` soft-deprecates (sets `invalid_at`)
    memories whose effective strength has fallen below a floor. Never deletes.

Spaced repetition lives on the read path (retrieval resets `accessed_at`), so a
retrieved memory's Δt → 0 and its strength recovers.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..models import utcnow_iso


def _age_days(accessed_at: str, now: str) -> float:
    start = datetime.fromisoformat(accessed_at)
    end = datetime.fromisoformat(now)
    return max((end - start).total_seconds() / 86400.0, 0.0)


def effective_strength(
    strength: float, accessed_at: str, now: str, lambda_per_day: float
) -> float:
    """Ebbinghaus-style decay: base strength × exp(-λ · days since last access)."""
    return strength * math.exp(-lambda_per_day * _age_days(accessed_at, now))


_SWEEP = """
FOR m IN memories
  FILTER m.type == "episodic" AND m.invalid_at == null
  LET age_days = DATE_DIFF(m.accessed_at, @now, "s") / 86400.0
  LET eff = m.strength * EXP(@neg_lam * age_days)
  FILTER eff < @floor
  UPDATE m WITH { invalid_at: @now } IN memories
  RETURN 1
"""


def decay_sweep(
    db: StandardDatabase, *, lambda_per_day: float, floor: float
) -> int:
    """Soft-deprecate episodic memories below the strength floor. Returns count."""
    now = utcnow_iso()
    bind_vars: dict[str, Any] = {"now": now, "neg_lam": -lambda_per_day, "floor": floor}
    cursor = cast(Cursor, db.aql.execute(_SWEEP, bind_vars=bind_vars))
    return len(list(cursor))


def reset_access(db: StandardDatabase, memory_keys: list[str]) -> None:
    """Spaced repetition: mark retrieved memories accessed now (Δt → 0)."""
    if not memory_keys:
        return
    now = utcnow_iso()
    bind_vars: dict[str, Any] = {"keys": memory_keys, "now": now}
    db.aql.execute(
        """
        FOR k IN @keys
          LET doc = DOCUMENT(memories, k)
          FILTER doc != null
          UPDATE doc WITH { accessed_at: @now, access_count: (doc.access_count || 1) + 1 }
            IN memories
        """,
        bind_vars=bind_vars,
    )
