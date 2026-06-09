"""Conflict resolution mechanism (DESIGN.md §12).

`supersede` is the bi-temporal primitive: a new entity supersedes an old one via
a `Supersedes` edge (new → old) and the old entity is soft-deprecated
(`invalid_at` set, never deleted — retained for audit). It is intentionally just
the mechanism; the *decision* to supersede (confirming an ambiguous conflict) is
Dream State's job in Step 4c, which calls this after review.
"""

from __future__ import annotations

from arango.database import StandardDatabase

from ..models import utcnow_iso


def supersede(db: StandardDatabase, *, new_key: str, old_key: str) -> None:
    """Record `new` superseding `old` and soft-deprecate `old`. Idempotent."""
    now = utcnow_iso()
    db.collection("Supersedes").insert(
        {
            "_key": f"{new_key}__{old_key}",
            "_from": f"entities/{new_key}",
            "_to": f"entities/{old_key}",
            "relationship": "supersedes",
            "ingestion_time": now,
            "valid_time": now,
            "valid_time_explicit": False,
            "invalid_at": None,
            "weight": 1.0,
        },
        overwrite_mode="ignore",
        silent=True,
    )
    db.collection("entities").update({"_key": old_key, "invalid_at": now})
