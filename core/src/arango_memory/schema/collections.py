"""Schema bootstrap — idempotent `ensure_schema()` run at startup (DESIGN.md §6).

Step 0 creates the minimal walking-skeleton schema: the `episodes`, `memories`,
and `entities` document collections, unique idempotency indexes, and the
`memory_search_view` ArangoSearch view used for BM25 retrieval. Vector indexes,
edge collections, and the migration runner are added in later steps.
"""

from __future__ import annotations

from typing import Any, cast

from arango.database import StandardDatabase
from arango.exceptions import IndexCreateError

from .migrations import run_migrations

DOCUMENT_COLLECTIONS: tuple[str, ...] = (
    "episodes", "memories", "entities", "steps", "ontology_proposals",
)
EDGE_COLLECTIONS: tuple[str, ...] = (
    "mentions", "relates_to", "produced_by", "TOUCHED", "TRANSITION", "Supersedes",
)

# Dead-letter for writes that exhaust retries (DESIGN.md §15). Named without a
# leading underscore (ArangoDB reserves "_*" for system collections).
DEAD_LETTER_COLLECTION = "failed_writes"

SEARCH_VIEW = "memory_search_view"
VECTOR_FIELD = "embedding"
VECTOR_INDEX_NAME = "idx_vector"

GRAPH_NAME = "memory_graph"
_EDGE_DEFINITIONS = [
    {
        "edge_collection": "mentions",
        "from_vertex_collections": ["memories"],
        "to_vertex_collections": ["entities"],
    },
    {
        "edge_collection": "relates_to",
        "from_vertex_collections": ["entities"],
        "to_vertex_collections": ["entities"],
    },
    {
        "edge_collection": "produced_by",
        "from_vertex_collections": ["entities"],
        "to_vertex_collections": ["episodes"],
    },
    {
        "edge_collection": "TOUCHED",
        "from_vertex_collections": ["steps"],
        "to_vertex_collections": ["memories"],
    },
    {
        "edge_collection": "TRANSITION",
        "from_vertex_collections": ["steps"],
        "to_vertex_collections": ["steps"],
    },
    {
        "edge_collection": "Supersedes",
        "from_vertex_collections": ["entities"],
        "to_vertex_collections": ["entities"],
    },
]


def ensure_schema(db: StandardDatabase) -> None:
    """Create collections, indexes, and the search view if absent. Idempotent."""
    for name in (*DOCUMENT_COLLECTIONS, DEAD_LETTER_COLLECTION):
        if not db.has_collection(name):
            db.create_collection(name)
    for name in EDGE_COLLECTIONS:
        if not db.has_collection(name):
            db.create_collection(name, edge=True)

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
    # Working-memory TTL (DESIGN.md §5/§14): auto-expire docs once `expires_at`
    # passes. Episodic memories store expires_at=null, which the TTL index ignores.
    db.collection("memories").add_index(
        {"type": "ttl", "fields": ["expires_at"], "expireAfter": 0, "name": "idx_working_ttl"}
    )
    # Entity natural-key dedup for UPSERT (DESIGN.md §5, §8).
    db.collection("entities").add_index(
        {
            "type": "persistent",
            "fields": ["tenant_id", "name", "label"],
            "unique": True,
            "name": "idx_entity_natural_key",
        }
    )
    # Step natural-key dedup for UPSERT + use_count reuse (DESIGN.md §5, §11).
    db.collection("steps").add_index(
        {
            "type": "persistent",
            "fields": ["tenant_id", "agent_id", "tool_name", "outcome"],
            "unique": True,
            "name": "idx_step_natural_key",
        }
    )

    _ensure_search_view(db)
    if not db.has_graph(GRAPH_NAME):
        db.create_graph(GRAPH_NAME, edge_definitions=_EDGE_DEFINITIONS)

    # Apply any versioned migrations on top of the idempotent baseline (§6).
    run_migrations(db)


def has_vector_index(db: StandardDatabase) -> bool:
    """True if the Faiss IVF index on `memories.embedding` exists (DESIGN.md §7)."""
    indexes = cast("list[dict[str, Any]]", db.collection("memories").indexes())
    return any(idx.get("type") == "vector" for idx in indexes)


def drop_vector_index(db: StandardDatabase) -> bool:
    """Drop the Faiss IVF index if present (retrieval self-heals). Returns True if dropped."""
    memories = db.collection("memories")
    for idx in cast("list[dict[str, Any]]", memories.indexes()):
        if idx.get("type") == "vector":
            memories.delete_index(idx["id"])
            return True
    return False


def ensure_vector_index(db: StandardDatabase, *, dimensions: int, n_lists: int) -> bool:
    """Create the Faiss IVF index on `memories.embedding` if warm enough.

    The index can only be built once the corpus has ≥ `n_lists` documents
    (ArangoDB raises ERR 1555 "vector index not ready" otherwise). Returns True
    if the index exists (or was just created), False if creation was deferred —
    in which case retrieval falls back to BM25 (DESIGN.md §7, §15).
    """
    if has_vector_index(db):
        return True
    # Only attempt creation once warm enough to train; below the threshold the
    # build raises ERR 1555 and can leave a phantom index behind. The shared
    # index trains on the aggregate corpus across tenants (§7), so count is total.
    if cast(int, db.collection("memories").count()) < n_lists:
        return False
    try:
        db.collection("memories").add_index(
            {
                "type": "vector",
                "name": VECTOR_INDEX_NAME,
                "fields": [VECTOR_FIELD],
                "params": {"metric": "cosine", "dimension": dimensions, "nLists": n_lists},
            }
        )
        return True
    except IndexCreateError:
        return False


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
                        "prospective_queries": {"analyzers": ["text_en"]},
                        "tenant_id": {"analyzers": ["identity"]},
                        "agent_id": {"analyzers": ["identity"]},
                    },
                },
            },
            "commitIntervalMsec": 1000,
            "consolidationIntervalMsec": 10000,
        },
    )
