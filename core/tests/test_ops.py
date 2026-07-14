"""Ops CLI functions: vector rebuild, embeddings migrate, dead-letter replay (Step 7b)."""

from __future__ import annotations

from arango.database import StandardDatabase

from arango_memory.config import settings
from arango_memory.embedding import FakeEmbedder
from arango_memory.ingest.store import store
from arango_memory.models import utcnow_iso
from arango_memory.ops import (
    _build_parser,
    explain_hot_queries,
    migrate_embeddings,
    rebuild_vector_index,
    replay_dead_letters,
)
from arango_memory.schema.collections import has_vector_index

_N_LISTS = 16


class _EmbedderV2(FakeEmbedder):
    """Same hashing as FakeEmbedder but a new model version (simulates a model change)."""

    def __init__(self) -> None:
        super().__init__(dimensions=settings.embedding_dimensions)
        self.version = "2"


def test_rebuild_vector_index_on_warm_corpus(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "ops_v", "agent_id": "a"}
    for i in range(40):
        store(db, content=f"memory {i} about subject {i}", turn_index=i, **ctx)

    built = rebuild_vector_index(db, dimensions=settings.embedding_dimensions, n_lists=_N_LISTS)
    assert built is True
    assert has_vector_index(db) is True


def test_migrate_embeddings_reembeds_only_stale(db: StandardDatabase) -> None:
    ctx = {"tenant_id": "ops_m", "agent_id": "a"}
    store(db, content="Alice met Bob", **ctx)  # embedded at version "1" (default fake)

    counts = migrate_embeddings(db, embedder=_EmbedderV2(), n_lists=_N_LISTS)
    assert counts["memories"] >= 1
    assert counts["entities"] >= 2  # Alice, Bob

    memory = next(db.aql.execute("FOR m IN memories FILTER m.tenant_id == 'ops_m' RETURN m"))
    assert memory["embedding_version"] == "2"

    # Re-running is a no-op (nothing stale left).
    again = migrate_embeddings(db, embedder=_EmbedderV2(), n_lists=_N_LISTS)
    assert again == {"memories": 0, "entities": 0}


def test_replay_dead_letters_commits_and_clears(db: StandardDatabase) -> None:
    intent = {
        "content": "Replay this fact",
        "tenant_id": "ops_r",
        "agent_id": "a",
        "session_id": None,
        "turn_index": 0,
        "attempts": 1,
    }
    db.collection("failed_writes").insert(
        {"_key": "dl1", "kind": "write", "intent": intent, "error": "boom",
         "attempts": 1, "failed_at": utcnow_iso()}
    )

    replayed = replay_dead_letters(db)
    assert replayed == 1
    assert db.collection("failed_writes").count() == 0
    committed = list(db.aql.execute("FOR m IN memories FILTER m.tenant_id == 'ops_r' RETURN m"))
    assert committed != []


def test_explain_hot_queries_use_indexes_not_full_scans(db: StandardDatabase) -> None:
    rows = explain_hot_queries(db)
    assert {row["query"] for row in rows}  # non-empty audit set
    offenders = [row["query"] for row in rows if row["full_scan"]]
    assert offenders == [], f"hot queries falling back to full collection scan: {offenders}"
    # Every audited query resolves through a named persistent index.
    assert all(row["indexes"] for row in rows)


def test_cli_parser_recognizes_commands() -> None:
    parser = _build_parser()
    assert parser.parse_args(["vector-rebuild"]).command == "vector-rebuild"
    assert parser.parse_args(["embeddings-migrate"]).command == "embeddings-migrate"
    assert parser.parse_args(["replay"]).command == "replay"
    assert parser.parse_args(["explain"]).command == "explain"
    assert parser.parse_args(["vector-diag"]).command == "vector-diag"
