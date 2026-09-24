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
  mem-sample         print arangod memory metrics as JSON lines, every --interval seconds
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase
from arango.exceptions import ArangoError

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


# Server-wide memory metrics (ArangoDB 3.12 names) sampled by `mem-sample`. `untracked` is RSS
# minus the rest: heap the allocator kept after a peak, plus anything without a metric.
_MEMORY_METRICS = {
    "rss": "arangodb_process_statistics_resident_set_size",
    "block_cache": "rocksdb_block_cache_usage",
    "memtables": "rocksdb_size_all_mem_tables",
    "cache": "rocksdb_cache_allocated",
    "aql": "arangodb_aql_global_memory_usage",
    "index_estimates": "arangodb_index_estimates_memory_usage",
}


def parse_metrics(text: str) -> dict[str, float]:
    """Prometheus text → {metric name: value summed over its label sets}."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        series, _, value = line.rpartition(" ")
        name = series.split("{", 1)[0]
        try:
            out[name] = out.get(name, 0.0) + float(value)
        except ValueError:
            continue
    return out


def memory_sample(client: ArangoMemoryClient) -> dict[str, Any]:
    """One snapshot of arangod memory (MiB) plus the entities/memories and vector indexes held
    across every database — so a climb can be matched to what the workload had loaded."""
    sys_db = client.database("_system")
    metrics = parse_metrics(cast(str, sys_db.metrics()))
    mib = {k: round(metrics.get(name, 0.0) / 2**20) for k, name in _MEMORY_METRICS.items()}
    mib["untracked"] = mib["rss"] - sum(v for k, v in mib.items() if k != "rss")
    held = {"entities": 0, "memories": 0, "vector_indexes": 0}
    for name in cast("list[str]", sys_db.databases()):
        try:  # a database can be dropped between listing and counting
            db = client.database(name)
            for coll in ("entities", "memories"):
                if db.has_collection(coll):
                    held[coll] += cast(int, db.collection(coll).count())
                    indexes = cast("list[dict[str, Any]]", db.collection(coll).indexes())
                    held["vector_indexes"] += sum(i.get("type") == "vector" for i in indexes)
        except ArangoError:
            continue
    return {"ts": utcnow_iso(), **{f"{k}_mib": v for k, v in mib.items()}, **held}


def _sample_memory(interval: float, count: int) -> None:
    client = ArangoMemoryClient()
    taken = 0
    while True:
        try:
            row = memory_sample(client)
        except Exception as exc:  # keep sampling through an outage — the OOM is the event
            row = {"ts": utcnow_iso(), "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(row), flush=True)
        taken += 1
        if count and taken >= count:
            return
        time.sleep(interval)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arango_memory.ops")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("vector-rebuild", help="drop + recreate the Faiss vector index")
    sub.add_parser("embeddings-migrate", help="re-embed stale docs, then rebuild the index")
    sub.add_parser("replay", help="re-enqueue + commit dead-lettered writes")
    sub.add_parser("explain", help="EXPLAIN hot-path queries; flag full-collection scans")
    sub.add_parser("vector-diag", help="probe the vector arm; print the raw failure reason")
    mem = sub.add_parser("mem-sample", help="print arangod memory metrics as JSON lines")
    mem.add_argument("--interval", type=float, default=30.0, help="seconds between samples")
    mem.add_argument("--count", type=int, default=0, help="stop after N samples (0 = forever)")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Configure logging first so any degradation prints the real reason, not a bare
    # class name (MA-8) — the opacity that stalled the P1 benchmark.
    configure_logging()
    args = _build_parser().parse_args(argv)
    if args.command == "mem-sample":  # server-wide and read-only: don't create ARANGO_DB
        _sample_memory(args.interval, args.count)
        return 0
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
