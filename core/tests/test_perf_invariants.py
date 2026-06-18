"""Deterministic perf-regression gate (DESIGN.md §22/§23).

Structural invariants, not wall-clock thresholds — stable on shared CI runners.
They catch the regressions that actually hurt latency/cost: per-item embedding
calls (should be one batch per write) and per-result query fan-out (the arm count
must not grow with the corpus).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest
from arango.aql import AQL
from arango.database import StandardDatabase

from arango_memory.embedding import FakeEmbedder
from arango_memory.embedding_cache import embedding_cache
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult, retrieve


class _CountingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__(dimensions=64)
        self.embed_calls = 0
        self.batch_calls = 0

    def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        return super().embed(text)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        self.batch_calls += 1
        base = FakeEmbedder.embed
        return [base(self, t) for t in texts]


def test_write_embeds_entities_in_one_batch(db: StandardDatabase) -> None:
    embedding_cache.clear()
    emb = _CountingEmbedder()

    # Two writes, the second with strictly more entities. Per write the entity
    # names must cost exactly one embed_batch call (not one embed() per name) and
    # the content costs exactly one embed() — independent of entity count.
    store(db, content="Alice met Bob in Paris", tenant_id="perf_w", agent_id="a",
          turn_index=0, embedder=emb)
    assert (emb.embed_calls, emb.batch_calls) == (1, 1)

    store(db, content="Cara saw Dan and Eve and Finn in Rome", tenant_id="perf_w", agent_id="a",
          turn_index=1, embedder=emb)
    assert (emb.embed_calls, emb.batch_calls) == (2, 2)  # +1 each — no per-entity scaling


def test_retrieve_aql_fan_out_does_not_grow_with_corpus(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = {"tenant_id": "perf_r", "agent_id": "a"}
    for i in range(3):
        store(db, content=f"Alice met Bob about topic {i}", turn_index=i, **ctx)
    wait_for_searchable(db, query="Alice", **ctx)

    calls = {"n": 0}
    original = AQL.execute

    def counting(self: AQL, *args: object, **kwargs: object) -> object:
        calls["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AQL, "execute", counting)

    calls["n"] = 0
    retrieve(db, query="Alice Bob", **ctx)
    small = calls["n"]

    # Grow the corpus ~5x, then retrieve again: the arm count is fixed (BM25 + graph
    # + access reset), so it must not scale with the number of stored memories.
    monkeypatch.setattr(AQL, "execute", original)  # don't count the seeding writes
    for i in range(3, 18):
        store(db, content=f"Alice met Bob about topic {i}", turn_index=i, **ctx)
    wait_for_searchable(db, query="Alice", **ctx)
    monkeypatch.setattr(AQL, "execute", counting)

    calls["n"] = 0
    retrieve(db, query="Alice Bob", **ctx)
    large = calls["n"]

    assert small == large, f"AQL fan-out grew with corpus: {small} → {large} (N+1?)"
    assert large <= 4, f"unexpectedly many AQL queries per retrieve: {large}"
