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
    "episodes", "memories", "entities", "steps", "ontology_proposals", "sessions",
    "write_intents",
)
EDGE_COLLECTIONS: tuple[str, ...] = (
    "mentions", "relates_to", "produced_by", "TOUCHED", "TRANSITION", "Supersedes",
)

# Dead-letter for writes that exhaust retries (DESIGN.md §15). Named without a
# leading underscore (ArangoDB reserves "_*" for system collections).
DEAD_LETTER_COLLECTION = "failed_writes"

# Durable write-queue backlog (DESIGN.md §15) — used by the ArangoQueue backend.
WRITE_QUEUE_COLLECTION = "write_intents"

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
    _ensure_scope_indexes(db)

    _ensure_search_view(db)
    if not db.has_graph(GRAPH_NAME):
        db.create_graph(GRAPH_NAME, edge_definitions=_EDGE_DEFINITIONS)

    # Apply any versioned migrations on top of the idempotent baseline (§6).
    run_migrations(db)


def _ensure_scope_indexes(db: StandardDatabase) -> None:
    """Persistent indexes backing the hot-path scope filters (DESIGN.md §6 audit).

    Every retrieval arm, lifecycle pass, and admin scan filters on some prefix of
    `(tenant_id, agent_id, invalid_at)` (or a collection-specific key). The unique
    natural-key / idempotency indexes only cover their own field orders, so without
    these a tenant-scoped query degrades to a full collection scan. Non-unique
    persistent indexes; idempotent (re-adding an existing index is a no-op).
    """
    for collection, name, fields in (
        # Vector arm + working buffer + forget + stats all scope memories this way.
        ("memories", "idx_mem_scope", ["tenant_id", "agent_id", "invalid_at"]),
        # Dream / community / salience / ontology / entity API / forget scan entities.
        ("entities", "idx_entity_scope", ["tenant_id", "invalid_at"]),
        # LangChain history reads a session's episodes in order.
        ("episodes", "idx_episode_session", ["tenant_id", "agent_id", "session_id"]),
        # ArangoQueue claim/pending scan for expired or unleased intents (§15).
        ("write_intents", "idx_intent_lease", ["leased_until"]),
        # Ontology review lists proposals by tenant + status.
        ("ontology_proposals", "idx_proposal_scope", ["tenant_id", "status"]),
    ):
        db.collection(collection).add_index(
            {"type": "persistent", "fields": fields, "name": name}
        )


def has_vector_index(db: StandardDatabase) -> bool:
    """True if the Faiss IVF index on `memories.embedding` exists (DESIGN.md §7)."""
    indexes = cast("list[dict[str, Any]]", db.collection("memories").indexes())
    return any(idx.get("type") == "vector" for idx in indexes)


def vector_index_state(db: StandardDatabase) -> str:
    """The vector arm's state for /health (MA-8): 'trained' if the Faiss index exists,
    else 'deferred' (corpus below the training threshold → BM25-only for now). A cheap
    index-metadata check, safe to call on a liveness probe."""
    return "trained" if has_vector_index(db) else "deferred"


def drop_vector_index(db: StandardDatabase) -> bool:
    """Drop the Faiss IVF index if present (retrieval self-heals). Returns True if dropped."""
    memories = db.collection("memories")
    for idx in cast("list[dict[str, Any]]", memories.indexes()):
        if idx.get("type") == "vector":
            memories.delete_index(idx["id"])
            return True
    return False


def vector_training_threshold(n_lists: int, train_factor: int) -> int:
    """Docs the corpus needs before the IVF index is built: `n_lists × train_factor`.

    ArangoDB raises ERR 1555 below `n_lists` docs, but training *at* `n_lists` gives
    one point per centroid — useless recall. `train_factor` (MA-8) holds off until the
    centroids have enough points to train on. Always ≥ `n_lists` so the ERR-1555 floor
    is respected even if `train_factor` is 1.
    """
    return max(n_lists, n_lists * train_factor)


def ensure_vector_index(
    db: StandardDatabase, *, dimensions: int, n_lists: int, train_factor: int = 1
) -> bool:
    """Create the Faiss IVF index on `memories.embedding` if warm enough.

    Deferred until the corpus reaches `vector_training_threshold(n_lists, train_factor)`
    documents, so the IVF centroids train on enough points (DESIGN.md §7, MA-8). Returns
    True if the index exists (or was just created), False if creation was deferred — in
    which case retrieval falls back to BM25 (DESIGN.md §7, §15).
    """
    if has_vector_index(db):
        return True
    # Only attempt creation once warm enough to train; below the threshold the build is
    # either rejected (ERR 1555) or badly under-trained. The shared index trains on the
    # aggregate corpus across tenants (§7), so count is total.
    corpus = cast(int, db.collection("memories").count())
    if corpus < vector_training_threshold(n_lists, train_factor):
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
