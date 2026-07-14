"""Integration tests for the vector-index lifecycle and fused retrieval (DESIGN.md §7, §9).

Uses the default fake embedder (dimensions from settings) so store, query, and
the index all agree on dimensionality. A small n_lists keeps the training
threshold low enough for a fast test.
"""

from __future__ import annotations

from collections.abc import Callable

from arango.database import StandardDatabase

from arango_memory.config import settings
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult, diagnose_vector, retrieve
from arango_memory.schema.collections import (
    ensure_vector_index,
    has_vector_index,
    vector_index_state,
    vector_training_threshold,
)

_N_LISTS = 16


def test_vector_training_threshold_math() -> None:
    # factor 1 → just the ERR-1555 floor; higher factors hold off until well-trained (MA-8).
    assert vector_training_threshold(64, 1) == 64
    assert vector_training_threshold(64, 40) == 64 * 40
    # never below n_lists even for a degenerate factor.
    assert vector_training_threshold(16, 0) == 16


def test_ensure_vector_index_defers_below_train_factor(db: StandardDatabase) -> None:
    # 5 docs clears the raw n_lists floor (4) but not the training threshold (4×3=12),
    # so the index defers rather than building an under-trained one (MA-8).
    dims = settings.embedding_dimensions
    for i in range(5):
        store(db, content=f"doc {i}", tenant_id="t_factor", agent_id="a", turn_index=i)
    assert ensure_vector_index(db, dimensions=dims, n_lists=4, train_factor=3) is False
    assert has_vector_index(db) is False
    assert vector_index_state(db) == "deferred"


def test_diagnose_vector_reports_state_and_no_raw_error_when_healthy(db: StandardDatabase) -> None:
    store(db, content="a memory to retrieve", tenant_id="t_diag", agent_id="a")
    report = diagnose_vector(db, tenant_id="t_diag")
    assert report["ok"] is True  # BM25 arm works even with the vector index deferred
    assert report["index_state"] == "deferred"
    assert report["training_threshold"] == vector_training_threshold(
        settings.vector_n_lists, settings.vector_train_factor
    )
    assert "error" not in report


def test_vector_index_deferred_until_warm_then_fuses(db: StandardDatabase) -> None:
    dims = settings.embedding_dimensions
    ctx = {"tenant_id": "t_vec", "agent_id": "a"}

    # Cold start: one doc < n_lists → index creation deferred (DESIGN.md §7).
    store(db, content="lonely first memory", **ctx)
    assert ensure_vector_index(db, dimensions=dims, n_lists=_N_LISTS) is False
    assert has_vector_index(db) is False

    # Warm up past the training threshold.
    for i in range(40):
        store(db, content=f"memory {i} discusses subject {i} in detail", turn_index=i + 1, **ctx)
    assert ensure_vector_index(db, dimensions=dims, n_lists=_N_LISTS) is True
    assert has_vector_index(db) is True

    # Retrieval now fuses BM25 + vector; the vector signal must contribute.
    result = retrieve(
        db, query="subject 7 in detail", tenant_id="t_vec", agent_id="a", k=5, candidate_pool=50
    )
    assert result.hits
    assert any("vector" in h.source for h in result.hits)


def test_bm25_fallback_when_index_cold(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    store(
        db,
        content="arctic penguins huddle together for warmth",
        tenant_id="t_cold",
        agent_id="a",
    )
    assert has_vector_index(db) is False

    result = wait_for_searchable(db, query="penguins warmth", tenant_id="t_cold", agent_id="a")
    assert result.hits
    assert all(h.source == "bm25" for h in result.hits)
