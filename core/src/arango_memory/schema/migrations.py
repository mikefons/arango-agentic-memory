"""Schema migration runner (DESIGN.md §6 startup step 2).

`ensure_schema` is the idempotent **baseline** bootstrap. This runner sits on
top for **versioned deltas** going forward: it records the applied version in a
`meta` collection (named without the leading underscore — ArangoDB reserves
`_*` for system collections, so this deviates from §5's `_meta`) and applies any
registered migrations whose version exceeds it, in order, exactly once.

`MIGRATIONS` is empty at v1 — the current schema is the idempotent baseline.
Future schema changes append a `Migration` (and bump nothing else; the version
is the migration's own number).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from arango.database import StandardDatabase

META_COLLECTION = "meta"
_VERSION_KEY = "schema_version"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[StandardDatabase], None]


# Ordered registry. Empty at v1 (baseline = idempotent ensure_schema).
MIGRATIONS: list[Migration] = []


def _applied_version(db: StandardDatabase) -> int:
    if not db.has_collection(META_COLLECTION):
        db.create_collection(META_COLLECTION)
    doc = cast("dict[str, Any] | None", db.collection(META_COLLECTION).get(_VERSION_KEY))
    return int(doc["value"]) if doc else 0


def _set_version(db: StandardDatabase, version: int) -> None:
    db.collection(META_COLLECTION).insert(
        {"_key": _VERSION_KEY, "value": version},
        overwrite_mode="replace",
        silent=True,
    )


def run_migrations(
    db: StandardDatabase, migrations: Sequence[Migration] | None = None
) -> int:
    """Apply pending migrations in version order. Returns the applied version."""
    pending = MIGRATIONS if migrations is None else migrations
    applied = _applied_version(db)
    for migration in sorted(pending, key=lambda m: m.version):
        if migration.version > applied:
            migration.apply(db)
            applied = migration.version
            _set_version(db, applied)
    return applied
