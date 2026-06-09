"""Lifecycle metrics: decay/consolidation/conflict, cache hit-rate, stats (DESIGN.md §18)."""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.generation import FakeGenerator
from arango_memory.ingest.store import store
from arango_memory.lifecycle.decay import decay_sweep
from arango_memory.lifecycle.dream import run_dream_state
from arango_memory.retrieve.enrich import QueryCache
from arango_memory.stats import stats
from arango_memory.telemetry import metrics


@pytest.fixture(autouse=True)
def _clear_metrics() -> Iterator[None]:
    metrics.clear()
    yield
    metrics.clear()


class _Stub:
    """Embedder with controllable cosine similarity (for conflict detection)."""

    model = "stub"
    version = "1"
    dimensions = 3

    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table

    def embed(self, text: str) -> list[float]:
        return self._table.get(text, [0.0, 0.0, 1.0])

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


def _days_ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


# ── cache hit-rate (unit) ─────────────────────────────────
def test_query_cache_emits_hit_rate() -> None:
    events: list[dict[str, object]] = []
    metrics.on("cache", lambda **p: events.append(p))
    cache = QueryCache()

    assert cache.get_gate("q") is None  # miss
    cache.set_gate("q", True)
    assert cache.get_gate("q") is True  # hit

    assert [e["hit"] for e in events] == [False, True]
    assert events[-1]["hit_rate"] == 0.5


# ── lifecycle counters (integration) ──────────────────────
def test_decay_sweep_emits_pruned(db: StandardDatabase) -> None:
    store(db, content="bravo drop", tenant_id="m_decay", agent_id="a")
    key = next(db.aql.execute("FOR m IN memories FILTER m.tenant_id == 'm_decay' RETURN m._key"))
    db.collection("memories").update({"_key": key, "accessed_at": _days_ago(500)})

    events: list[dict[str, object]] = []
    metrics.on("decay", lambda **p: events.append(p))
    decay_sweep(db, lambda_per_day=0.02, floor=0.1)
    assert events and events[0]["pruned"] >= 1


def test_conflict_detection_emits_detected(db: StandardDatabase) -> None:
    emb = _Stub({"Alice": [1.0, 0.0, 0.0], "Alfred": [0.7, 0.714, 0.0]})  # cos ≈ 0.70 → flag band
    store(db, content="Alice", tenant_id="m_conf", agent_id="a", turn_index=0, embedder=emb)

    events: list[dict[str, object]] = []
    metrics.on("conflict", lambda **p: events.append(p))
    store(db, content="Alfred", tenant_id="m_conf", agent_id="a", turn_index=1, embedder=emb)
    assert events and events[0]["detected"] == 1


def test_dream_emits_consolidation(db: StandardDatabase) -> None:
    store(db, content="Acme Corp and Globex Inc", tenant_id="m_dream", agent_id="a")
    keys = {
        r["name"]: r["_key"]
        for r in db.aql.execute("FOR e IN entities FILTER e.tenant_id == 'm_dream' RETURN e")
    }
    db.collection("entities").update(
        {"_key": keys["Acme Corp"], "needs_review": True, "conflict_with": keys["Globex Inc"]}
    )

    events: list[dict[str, object]] = []
    metrics.on("consolidation", lambda **p: events.append(p))
    gen = FakeGenerator(handler=lambda p, s: "DISTINCT")
    run_dream_state(db, tenant_id="m_dream", generator=gen)
    assert events
    assert events[0]["cleared"] == 1
    assert events[0]["breaker_tripped"] is False


# ── stats / graph gauge ───────────────────────────────────
def test_stats_returns_counts_and_emits_graph(db: StandardDatabase) -> None:
    store(db, content="Alice met Bob", tenant_id="m_stats", agent_id="a")

    events: list[dict[str, object]] = []
    metrics.on("graph", lambda **p: events.append(p))
    counts = stats(db, tenant_id="m_stats")

    assert counts["memories"] >= 1
    assert counts["entities"] >= 2
    assert counts["episodes"] >= 1
    assert events and events[0]["tenant_id"] == "m_stats"
    assert events[0]["entity_count"] >= 2


def test_stats_endpoint_round_trip(api: TestClient) -> None:
    ctx = {"tenant_id": "m_se", "agent_id": "a", "access_level": "write"}
    api.post("/v1/store", json={"content": "alpha bravo", "ctx": ctx})

    counts: dict[str, int] = {}
    for _ in range(20):
        resp = api.get("/v1/stats", params={"tenant_id": "m_se"})
        assert resp.status_code == 200
        counts = resp.json()["counts"]
        if counts.get("memories", 0) >= 1:
            break
        time.sleep(0.25)
    assert counts["memories"] >= 1
