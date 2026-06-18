"""Failure injection — graceful degradation table (DESIGN.md §15).

Memory is an enhancement, never a dependency: injected faults must degrade to a
working (often memory-less) turn, never raise into the agent. Covers the embedder
outage (→ BM25-only retrieval) and an ArangoDB-unreachable write (→ dead-letter,
then clean replay once the DB is back).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from arango.database import StandardDatabase

from arango_memory.embedding import FakeEmbedder
from arango_memory.embedding_cache import embedding_cache
from arango_memory.generation import FakeGenerator
from arango_memory.ingest.queue import InProcessQueue, WriteIntent
from arango_memory.ingest.store import store
from arango_memory.ingest.worker import WriteWorker
from arango_memory.retrieve.search import RetrieveResult, retrieve
from arango_memory.schema.collections import ensure_schema
from arango_memory.telemetry import metrics


class _BoomEmbedder(FakeEmbedder):
    """Embedder whose provider calls always fail (models an embedding API outage)."""

    def embed(self, text: str) -> list[float]:
        raise RuntimeError("embedding provider down")

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("embedding provider down")


def _degraded_events() -> list[dict[str, object]]:
    seen: list[dict[str, object]] = []
    metrics.on("degraded", lambda **p: seen.append(p))
    return seen


# ── embedder outage → BM25-only (§15) ─────────────────────
def test_embedder_outage_degrades_to_bm25_lite(
    db: StandardDatabase, wait_for_searchable: Callable[..., RetrieveResult]
) -> None:
    ctx = {"tenant_id": "deg_lite", "agent_id": "a"}
    store(db, content="crimson lantern relic on the shelf", **ctx)
    wait_for_searchable(db, query="crimson", **ctx)

    embedding_cache.clear()  # force the query embed (no warm hit to mask the failure)
    events = _degraded_events()
    result = retrieve(db, query="crimson lantern", embedder=_BoomEmbedder(), **ctx)
    metrics.clear()

    assert result.hits, "BM25 arm should still answer when the embedder is down"
    assert any(e["op"] == "retrieve" for e in events)  # degradation was recorded


def test_embedder_outage_degrades_in_full_mode(
    db: StandardDatabase, wait_for_searchable: Callable[..., RetrieveResult]
) -> None:
    ctx = {"tenant_id": "deg_full", "agent_id": "a"}
    store(db, content="azure compass hidden in the vault", **ctx)
    wait_for_searchable(db, query="azure", **ctx)

    embedding_cache.clear()
    events = _degraded_events()
    # Full mode: gate runs (FakeGenerator → no skip), HyDE embed fails → falls back
    # to query text, which also fails to embed → BM25-only. Turn still succeeds.
    result = retrieve(
        db, query="azure compass", mode="full",
        embedder=_BoomEmbedder(), generator=FakeGenerator(), **ctx,
    )
    metrics.clear()

    assert result.hits
    assert any(e["op"] == "retrieve" for e in events)


# ── ArangoDB unreachable (write) → dead-letter + replay (§15) ──
def test_db_fault_mid_commit_dead_letters_then_replays(db: StandardDatabase) -> None:
    queue = InProcessQueue()
    worker = WriteWorker(queue, db, backoff_base=0.0, max_retries=2)
    queue.enqueue(WriteIntent(content="Alice met Bob in Paris", tenant_id="deg_db", agent_id="a"))

    # Inject the fault: the target collection vanishes mid-flight (DB unreachable).
    # Drop the graph first so `memories` is no longer a protected vertex collection.
    db.delete_graph("memory_graph", drop_collections=False)
    db.delete_collection("memories")
    assert worker.drain() == 1  # claimed + handled (failed)
    assert db.collection("failed_writes").count() == 1  # dead-lettered, turn unaffected

    # Recover the DB and replay — idempotency-keyed, so the replay commits cleanly.
    ensure_schema(db)
    assert worker.replay_failed() == 1
    assert worker.drain() == 1
    assert db.collection("failed_writes").count() == 0
    committed = next(db.aql.execute(
        "FOR m IN memories FILTER m.tenant_id == 'deg_db' COLLECT WITH COUNT INTO c RETURN c"
    ))
    assert committed >= 1
