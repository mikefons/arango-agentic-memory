"""WORM enforcement (DESIGN.md §17).

`episodes` are write-once provenance — INSERT-only, never UPDATE/DELETE via
application code. Inserts already use `overwrite_mode="ignore"` (a duplicate key
is a no-op, never an overwrite). `worm_guard` is the client-layer enforcement
point any future mutation path must call; the right-to-be-forgotten purge (5b)
is the one sanctioned bypass and will hard-delete deliberately, not via this API.
"""

from __future__ import annotations

WORM_COLLECTIONS: frozenset[str] = frozenset({"episodes"})


class WormViolation(RuntimeError):
    """Raised on an attempt to mutate a write-once collection."""


def worm_guard(collection: str) -> None:
    """Raise if `collection` is write-once (episodes). No-op otherwise."""
    if collection in WORM_COLLECTIONS:
        raise WormViolation(
            f"{collection!r} is write-once (WORM); UPDATE/DELETE is not permitted "
            "via application code (DESIGN.md §17)."
        )
