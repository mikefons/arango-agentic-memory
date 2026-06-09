"""Operational commands (DESIGN.md §7/Step 7b).

Admin/destructive maintenance, exposed as a CLI (`python -m arango_memory.ops
<command>`) rather than on the HTTP API. The logic lives in importable
functions; `main` is a thin argparse dispatch that connects via the env-driven
settings (like `arango_memory.check`).

Commands:
  vector-rebuild     drop + recreate the Faiss IVF index
  embeddings-migrate re-embed docs on a model change (stale only), then rebuild
  replay             re-enqueue + commit dead-lettered writes (§15)
"""

from __future__ import annotations

import argparse
from typing import cast

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
from .schema.collections import drop_vector_index, ensure_vector_index


def rebuild_vector_index(db: StandardDatabase, *, dimensions: int, n_lists: int) -> bool:
    """Drop the Faiss IVF index and recreate it from the current corpus."""
    drop_vector_index(db)
    return ensure_vector_index(db, dimensions=dimensions, n_lists=n_lists)


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
        db, dimensions=embedder.dimensions, n_lists=n_lists or settings.vector_n_lists
    )
    return counts


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    db = ArangoMemoryClient().connect()
    embedder = get_embedder()

    if args.command == "vector-rebuild":
        built = rebuild_vector_index(
            db, dimensions=embedder.dimensions, n_lists=settings.vector_n_lists
        )
        print("vector index: rebuilt" if built else "vector index: deferred (corpus < n_lists)")
    elif args.command == "embeddings-migrate":
        counts = migrate_embeddings(db, embedder=embedder)
        print(f"re-embedded: {counts}")
    elif args.command == "replay":
        replayed = replay_dead_letters(
            db, embedder=embedder, extractor=get_extractor(), generator=get_generator()
        )
        print(f"replayed: {replayed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
