"""Schema bootstrap — idempotent `ensure_schema()` run at startup (DESIGN.md §6).

Step 0 creates the minimal walking-skeleton schema: the `episodes`, `memories`,
and `entities` document collections, unique idempotency indexes, and the
`memory_search_view` ArangoSearch view used for BM25 retrieval. Vector indexes,
edge collections, and the migration runner are added in later steps.
"""

from __future__ import annotations

from typing import Any, cast

from arango.database import StandardDatabase

DOCUMENT_COLLECTIONS: tuple[str, ...] = ("episodes", "memories", "entities")

SEARCH_VIEW = "memory_search_view"


def ensure_schema(db: StandardDatabase) -> None:
    """Create collections, indexes, and the search view if absent. Idempotent."""
    for name in DOCUMENT_COLLECTIONS:
        if not db.has_collection(name):
            db.create_collection(name)

    # Unique idempotency index on episodes + memories (DESIGN.md §5).
    for name in ("episodes", "memories"):
        db.collection(name).add_index(
            {
                "type": "persistent",
                "fields": ["idempotency_key"],
                "unique": True,
                "name": "idx_idempotency",
            }
        )

    _ensure_search_view(db)


def _ensure_search_view(db: StandardDatabase) -> None:
    """ArangoSearch view over memory text (BM25) with tenant/agent scope fields."""
    existing = {v["name"] for v in cast("list[dict[str, Any]]", db.views())}
    if SEARCH_VIEW in existing:
        return

    db.create_arangosearch_view(
        name=SEARCH_VIEW,
        properties={
            "links": {
                "memories": {
                    "fields": {
                        "text": {"analyzers": ["text_en"]},
                        "tenant_id": {"analyzers": ["identity"]},
                        "agent_id": {"analyzers": ["identity"]},
                    },
                },
            },
            "commitIntervalMsec": 1000,
            "consolidationIntervalMsec": 10000,
        },
    )
