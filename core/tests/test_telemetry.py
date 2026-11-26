"""Observability: MemoryMetrics emitter + OTEL spans (DESIGN.md §18)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from arango.database import StandardDatabase

import arango_memory.retrieve.search as search_mod
from arango_memory.ingest.store import StoreItem, store, store_many
from arango_memory.retrieve.search import RetrieveResult, retrieve
from arango_memory.telemetry import LatencyRecorder, MemoryMetrics, StageTimer, latency, metrics


@pytest.fixture(autouse=True)
def _clear_metrics() -> Iterator[None]:
    metrics.clear()
    yield
    metrics.clear()


# ── emitter (unit) ────────────────────────────────────────
def test_emitter_dispatches_and_clears() -> None:
    m = MemoryMetrics()
    seen: list[dict[str, object]] = []
    m.on("x", lambda **p: seen.append(p))
    m.emit("x", a=1)
    m.emit("y", a=2)  # no handler → ignored
    assert seen == [{"a": 1}]
    m.clear()
    m.emit("x", a=3)
    assert seen == [{"a": 1}]  # handler removed


# ── latency recorder (unit) ───────────────────────────────
def test_latency_recorder_quantiles_nearest_rank() -> None:
    rec = LatencyRecorder()
    for ms in range(1, 101):  # 1..100ms
        rec.record("retrieval.lite", float(ms))
    snap = rec.snapshot()["retrieval.lite"]
    assert snap["count"] == 100
    assert snap["p50"] == 50.0
    assert snap["p95"] == 95.0
    assert snap["p99"] == 99.0


def test_latency_recorder_window_evicts_oldest() -> None:
    rec = LatencyRecorder(window=10)
    for ms in range(100):
        rec.record("write", float(ms))
    snap = rec.snapshot()["write"]
    assert snap["count"] == 10  # only the last 10 retained
    assert snap["p99"] == 99.0  # newest samples


def test_latency_snapshot_skips_empty_keys() -> None:
    rec = LatencyRecorder()
    assert rec.snapshot() == {}


def test_stage_timer_sums_repeated_stages_and_records_once() -> None:
    latency.clear()
    timer = StageTimer("op")
    for _ in range(3):
        with timer.stage("loop"):
            pass
    with timer.stage("once"):
        pass
    assert latency.snapshot() == {}  # nothing recorded until record()
    timer.record()
    snap = latency.snapshot()
    assert set(snap) == {"op.loop", "op.once"}
    assert snap["op.loop"]["count"] == 1  # one sample per operation, not per iteration


def test_stage_timer_records_a_stage_that_raised() -> None:
    latency.clear()
    timer = StageTimer("op")
    with pytest.raises(ValueError), timer.stage("boom"):
        raise ValueError("x")
    timer.record()
    assert "op.boom" in latency.snapshot()


def test_store_reports_where_the_write_time_went(db: StandardDatabase) -> None:
    latency.clear()
    store(
        db, content="Ada Lovelace worked with Charles Babbage in London.",
        tenant_id="t_stage", agent_id="a",
    )
    snap = latency.snapshot()
    for key in ("write", "write.episode", "write.embed", "write.memory", "write.entities"):
        assert key in snap, key
    assert "write.entities.extract" in snap
    assert snap["write.entities"]["p50"] >= snap["write.entities.extract"]["p50"]


def test_store_many_reports_where_a_batch_went(db: StandardDatabase) -> None:
    latency.clear()
    items = [
        StoreItem(content=f"Ada Lovelace met Charles Babbage in London, note {i}.", turn_index=i)
        for i in range(4)
    ]
    store_many(db, items, tenant_id="t_stage_many", agent_id="a", extract=True)
    snap = latency.snapshot()
    batch_keys = ("embed", "insert", "entities")
    for key in (f"write_many.{k}" for k in batch_keys):
        assert key in snap, key
    for stage in ("extract", "embed", "pool", "match", "upsert", "edges"):
        assert f"write_many.entities.{stage}" in snap, stage
    assert all(snap[k]["count"] == 1 for k in snap if k.startswith("write_many."))  # per batch


def test_retrieve_reports_where_a_read_went(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_retr_stage", "agent_id": "a"}
    store(db, content="Ada Lovelace worked with Charles Babbage in London.", **ctx)
    wait_for_searchable(db, query="Lovelace", **ctx)
    latency.clear()
    result = retrieve(db, query="Lovelace Babbage", **ctx)
    assert result.hits
    snap = latency.snapshot()
    stages = ("bm25", "vector_check", "fuse", "mmr", "neighbors", "assemble", "access")
    for stage in stages:
        assert f"retrieve.{stage}" in snap, stage
    assert all(snap[k]["count"] == 1 for k in snap if k.startswith("retrieve."))  # per read
    # a stage that did not run for this read has no sample (no vector index so no query
    # embedding, MMR at lambda=1.0, no reranker, no entity summaries)
    assert "retrieve.embed" not in snap
    assert "retrieve.mmr_embeddings" not in snap
    assert "retrieve.rerank" not in snap
    assert "retrieve.entities" not in snap


def test_retrieval_feeds_global_latency_per_mode(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    latency.clear()
    ctx = {"tenant_id": "t_lat", "agent_id": "a"}
    store(db, content="latency probe text", **ctx)
    wait_for_searchable(db, query="latency", **ctx)
    retrieve(db, query="latency", **ctx)
    snap = latency.snapshot()
    assert "retrieval.lite" in snap
    assert snap["retrieval.lite"]["count"] >= 1


# ── instrumentation (integration) ─────────────────────────
def test_retrieve_emits_retrieval_event(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_tel", "agent_id": "a"}
    store(db, content="alpha telemetry signal", **ctx)
    wait_for_searchable(db, query="telemetry", **ctx)  # warm up before registering

    events: list[dict[str, object]] = []
    metrics.on("retrieval", lambda **p: events.append(p))
    retrieve(db, query="telemetry", **ctx)

    assert len(events) == 1
    payload = events[0]
    assert payload.keys() >= {"duration_ms", "results_k", "tokens_injected", "mode"}
    assert payload["mode"] == "lite"
    assert payload["results_k"] >= 1


def test_store_emits_write_event(db: StandardDatabase) -> None:
    events: list[dict[str, object]] = []
    metrics.on("write", lambda **p: events.append(p))
    store(db, content="something", tenant_id="t_w", agent_id="a")
    assert any("duration_ms" in e for e in events)


def test_retrieve_degrades_to_emptyon_error(
    db: StandardDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> RetrieveResult:
        raise RuntimeError("backend down")

    monkeypatch.setattr(search_mod, "_retrieve_impl", boom)
    events: list[dict[str, object]] = []
    metrics.on("degraded", lambda **p: events.append(p))

    result = retrieve(db, query="x", tenant_id="t", agent_id="a")
    assert result.hits == [] and result.context == ""  # memory-less, no raise (§15)
    assert events and events[0]["op"] == "retrieve"


def test_retrieve_records_otel_span(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    retrieve(db, query="anything", tenant_id="t_span", agent_id="a")
    names = [s.name for s in exporter.get_finished_spans()]
    assert "memory.retrieve" in names


# ── OTEL meters (unit) ─────────────────────────────────────
class _Recorder:
    """Stands in for an OTEL counter/histogram, capturing (value, attributes)."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, dict[str, object] | None]] = []

    def add(self, amount: float, attributes: dict[str, object] | None = None) -> None:
        self.calls.append((amount, attributes))

    def record(self, amount: float, attributes: dict[str, object] | None = None) -> None:
        self.calls.append((amount, attributes))


@pytest.fixture
def meters(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Recorder]:
    """Swap the singleton's instruments for recorders so emits are observable."""
    from arango_memory.telemetry import _meters

    names = [
        "writes", "write_duration", "retrievals", "retrieval_duration",
        "retrieval_results", "retrieval_tokens", "degraded", "conflicts",
        "decay_pruned", "consolidations", "consolidation_changes", "cache_lookups",
    ]
    recorders = {name: _Recorder() for name in names}
    for name, rec in recorders.items():
        monkeypatch.setattr(_meters, name, rec)
    return recorders


def test_write_event_records_meters(meters: dict[str, _Recorder]) -> None:
    metrics.emit("write", duration_ms=12.5)
    metrics.emit("write", dead_lettered=True)
    assert meters["writes"].calls == [(1, {"outcome": "ok"}), (1, {"outcome": "dead_lettered"})]
    assert meters["write_duration"].calls == [(12.5, None)]


def test_retrieval_event_records_meters(meters: dict[str, _Recorder]) -> None:
    metrics.emit("retrieval", duration_ms=8.0, results_k=3, tokens_injected=120, mode="full")
    attrs = {"mode": "full"}
    assert meters["retrievals"].calls == [(1, attrs)]
    assert meters["retrieval_duration"].calls == [(8.0, attrs)]
    assert meters["retrieval_results"].calls == [(3, attrs)]
    assert meters["retrieval_tokens"].calls == [(120, attrs)]


def test_lifecycle_events_record_meters(meters: dict[str, _Recorder]) -> None:
    metrics.emit("degraded", op="retrieve", reason="Timeout")
    metrics.emit("conflict", detected=2)
    metrics.emit("decay", pruned=7)
    metrics.emit("cache", hit=True, hit_rate=0.5)
    metrics.emit("consolidation", promoted=1, superseded=2, cleared=0, breaker_tripped=False)

    assert meters["degraded"].calls == [(1, {"op": "retrieve", "reason": "Timeout"})]
    assert meters["conflicts"].calls == [(2, None)]
    assert meters["decay_pruned"].calls == [(7, None)]
    assert meters["cache_lookups"].calls == [(1, {"hit": True})]
    assert meters["consolidations"].calls == [(1, {"breaker_tripped": False})]
    # only non-zero change kinds are recorded
    assert meters["consolidation_changes"].calls == [
        (1, {"kind": "promoted"}),
        (2, {"kind": "superseded"}),
    ]
