"""Observability: MemoryMetrics emitter + OTEL spans (DESIGN.md §18)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from arango.database import StandardDatabase

import arango_memory.retrieve.search as search_mod
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult, retrieve
from arango_memory.telemetry import MemoryMetrics, metrics


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
