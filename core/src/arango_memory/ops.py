"""Operational commands (DESIGN.md §7/Step 7b).

Admin/destructive maintenance, exposed as a CLI (`python -m arango_memory.ops
<command>`) rather than on the HTTP API. The logic lives in importable
functions; `main` is a thin argparse dispatch that connects via the env-driven
settings (like `arango_memory.check`).

Commands:
  vector-rebuild     drop + recreate the Faiss IVF index
  embeddings-migrate re-embed docs on a model change (stale only), then rebuild
  replay             re-enqueue + commit dead-lettered writes (§15)
  explain            EXPLAIN the hot-path queries; flag full-collection scans (§6)
  vector-diag        probe the vector arm; print the raw failure reason (MA-8)
"""

from __future__ import annotations

import argparse
import json
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from .client import ArangoMemoryClient
from .config import settings
from .embedding import Embedder, get_embedder
from .generation import Generator, get_generator
from .ingest.extract import Extractor, get_extractor
from .ingest.queue import InProcessQueue
from .ingest.worker import WriteWorker
from .models import utcnow_iso
from .retrieve.search import diagnose_vector
from .schema.collections import drop_vector_index, ensure_vector_index
from .telemetry.logging import configure_logging


def rebuild_vector_index(
    db: StandardDatabase, *, dimensions: int, n_lists: int, train_factor: int = 1
) -> bool:
    """Drop the Faiss IVF index and recreate it from the current corpus. The training
    threshold (MA-8) still applies — a rebuild below it defers rather than building an
    under-trained index."""
    drop_vector_index(db)
    return ensure_vector_index(
        db, dimensions=dimensions, n_lists=n_lists, train_factor=train_factor
    )


def _reembed(db: StandardDatabase, collection: str, source_field: str, embedder: Embedder) -> int:
    """Re-embed docs in `collection` whose embedding_version is stale. Returns count."""
    query = (
        f"FOR d IN {collection} FILTER d.embedding_version != @v "
        f"RETURN {{ key: d._key, src: d.{source_field} }}"
    )
    stale = list(cast(Cursor, db.aql.execute(query, bind_vars={"v": embedder.version})))
    now = utcnow_iso()
    for row in stale:
        db.collection(collection).update(
            {
                "_key": row["key"],
                "embedding": embedder.embed(row["src"]),
                "embedding_model": embedder.model,
                "embedding_version": embedder.version,
                "reembedded_at": now,
            }
        )
    return len(stale)


def migrate_embeddings(
    db: StandardDatabase, *, embedder: Embedder, n_lists: int | None = None
) -> dict[str, int]:
    """Re-embed stale memories + entities to the embedder's model, then rebuild the index."""
    counts = {
        "memories": _reembed(db, "memories", "text", embedder),
        "entities": _reembed(db, "entities", "name", embedder),
    }
    rebuild_vector_index(
        db,
        dimensions=embedder.dimensions,
        n_lists=n_lists or settings.vector_n_lists,
        train_factor=settings.vector_train_factor,
    )
    return counts


# Representative hot-path queries (DESIGN.md §6 index audit). Each scopes a
# document collection by some prefix of (tenant_id, agent_id, invalid_at); EXPLAIN
# confirms the planner uses a persistent index rather than a full scan. Kept as
# self-contained skeletons (not the live constants) so EXPLAIN needs no warm
# corpus or vector index — the scope FILTER is what the audit checks.
_HOT_QUERIES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "memories scope (vector/working/forget arm)",
        "FOR doc IN memories FILTER doc.tenant_id == @t AND doc.agent_id == @a "
        "AND doc.invalid_at == null RETURN doc._key",
        {"t": "demo", "a": "default"},
    ),
    (
        "entities scope (dream/community/salience/ontology)",
        "FOR e IN entities FILTER e.tenant_id == @t AND e.invalid_at == null RETURN e._key",
        {"t": "demo"},
    ),
    (
        "episodes by session (langchain history)",
        "FOR e IN episodes FILTER e.tenant_id == @t AND e.agent_id == @a "
        "AND e.session_id == @s RETURN e._key",
        {"t": "demo", "a": "default", "s": "s1"},
    ),
    (
        "write_intents claim (durable queue)",
        "FOR d IN write_intents FILTER d.leased_until == null OR d.leased_until < @now "
        "RETURN d._key",
        {"now": "2026-01-01T00:00:00Z"},
    ),
    (
        "ontology_proposals list",
        "FOR p IN ontology_proposals FILTER p.tenant_id == @t AND p.status == @st RETURN p._key",
        {"t": "demo", "st": "pending"},
    ),
)


def explain_hot_queries(db: StandardDatabase) -> list[dict[str, object]]:
    """EXPLAIN each hot-path query; report whether the planner uses an index.

    Returns one row per query with the index names hit and a `full_scan` flag
    (an `EnumerateCollectionNode` in the plan means no index was used). Pure
    inspection — `explain` never executes the query.
    """
    rows: list[dict[str, object]] = []
    for label, query, bind_vars in _HOT_QUERIES:
        plan = cast("dict[str, Any]", db.aql.explain(query, bind_vars=bind_vars))
        nodes = cast("list[dict[str, Any]]", plan.get("nodes", []))
        indexes = [
            idx["name"]
            for node in nodes
            for idx in node.get("indexes", [])
            if node.get("type") == "IndexNode"
        ]
        full_scan = any(node.get("type") == "EnumerateCollectionNode" for node in nodes)
        rows.append({"query": label, "indexes": indexes, "full_scan": full_scan})
    return rows


def replay_dead_letters(
    db: StandardDatabase,
    *,
    embedder: Embedder | None = None,
    extractor: Extractor | None = None,
    generator: Generator | None = None,
) -> int:
    """Re-enqueue dead-lettered writes and commit them. Returns the count replayed."""
    queue = InProcessQueue()
    worker = WriteWorker(
        queue, db, embedder=embedder, extractor=extractor, generator=generator, backoff_base=0.0
    )
    replayed = worker.replay_failed()
    worker.drain()
    return replayed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arango_memory.ops")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("vector-rebuild", help="drop + recreate the Faiss vector index")
    sub.add_parser("embeddings-migrate", help="re-embed stale docs, then rebuild the index")
    sub.add_parser("replay", help="re-enqueue + commit dead-lettered writes")
    sub.add_parser("explain", help="EXPLAIN hot-path queries; flag full-collection scans")
    sub.add_parser("vector-diag", help="probe the vector arm; print the raw failure reason")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Configure logging first so any degradation prints the real reason, not a bare
    # class name (MA-8) — the opacity that stalled the P1 benchmark.
    configure_logging()
    args = _build_parser().parse_args(argv)
    db = ArangoMemoryClient().connect()
    embedder = get_embedder()

    if args.command == "vector-rebuild":
        built = rebuild_vector_index(
            db,
            dimensions=embedder.dimensions,
            n_lists=settings.vector_n_lists,
            train_factor=settings.vector_train_factor,
        )
        print(
            "vector index: rebuilt"
            if built
            else "vector index: deferred (corpus < n_lists × train_factor)"
        )
    elif args.command == "vector-diag":
        report = diagnose_vector(db, embedder=embedder)
        print(json.dumps(report, indent=2))
    elif args.command == "embeddings-migrate":
        counts = migrate_embeddings(db, embedder=embedder)
        print(f"re-embedded: {counts}")
    elif args.command == "replay":
        replayed = replay_dead_letters(
            db, embedder=embedder, extractor=get_extractor(), generator=get_generator()
        )
        print(f"replayed: {replayed}")
    elif args.command == "explain":
        for row in explain_hot_queries(db):
            status = "FULL SCAN" if row["full_scan"] else f"index={row['indexes']}"
            print(f"{'⚠' if row['full_scan'] else '✓'} {row['query']}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
