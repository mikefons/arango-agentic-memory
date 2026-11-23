"""Schema migration runner (DESIGN.md §6)."""

from __future__ import annotations

from arango.database import StandardDatabase

from arango_memory.schema.migrations import (
    META_COLLECTION,
    MIGRATIONS,
    Migration,
    _applied_version,
    run_migrations,
)

# `ensure_schema` (the db fixture) already applied the real migrations; synthetic ones sit above.
_BASE = max((m.version for m in MIGRATIONS), default=0)


def test_applies_in_version_order_and_records_version(db: StandardDatabase) -> None:
    calls: list[int] = []
    migrations = [
        Migration(_BASE + 2, "second", lambda d: calls.append(2)),
        Migration(_BASE + 1, "first", lambda d: calls.append(1)),
    ]
    applied = run_migrations(db, migrations=migrations)

    assert calls == [1, 2]  # sorted by version, not list order
    assert applied == _BASE + 2
    assert _applied_version(db) == _BASE + 2


def test_is_idempotent(db: StandardDatabase) -> None:
    calls: list[int] = []
    migrations = [Migration(_BASE + 1, "first", lambda d: calls.append(1))]
    run_migrations(db, migrations=migrations)
    run_migrations(db, migrations=migrations)

    assert calls == [1]  # applied exactly once
    assert _applied_version(db) == _BASE + 1


def test_applies_only_pending(db: StandardDatabase) -> None:
    calls: list[int] = []
    first = Migration(_BASE + 1, "first", lambda d: calls.append(1))
    second = Migration(_BASE + 2, "second", lambda d: calls.append(2))
    run_migrations(db, migrations=[first])          # → version 1
    run_migrations(db, migrations=[first, second])  # only 2 is pending

    assert calls == [1, 2]
    assert _applied_version(db) == _BASE + 2


def test_ensure_schema_creates_meta_at_baseline(db: StandardDatabase) -> None:
    # The `db` fixture already ran ensure_schema, which runs migrations.
    assert db.has_collection(META_COLLECTION)
    assert _applied_version(db) == max((m.version for m in MIGRATIONS), default=0)
