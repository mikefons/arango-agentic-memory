"""Observability (DESIGN.md §18): OTEL spans + meters + an in-process emitter.

Three backends, all opt-in and no-op without a configured OpenTelemetry SDK:
  - `span(name, **attrs)` — an OpenTelemetry span via the otel **API**, which is
    a no-op unless the user configures an SDK/exporter (so CI needs no collector).
  - OTEL **metric instruments** — counters + histograms (`memory.*`) recorded
    from every `metrics.emit(...)`, so any otel-compatible backend (Prometheus,
    Datadog, Grafana) gets cost/health signals with no call-site changes. Also
    no-op unless a `MeterProvider` is configured.
  - `metrics` — a `MemoryMetrics` event emitter for programmatic callbacks
    (`metrics.on("retrieval", handler)`); emits carry the cost/health payloads.

No built-in dashboard — users wire their own backend.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics as otel_metrics
from opentelemetry import trace
from opentelemetry.trace import Span

_tracer = trace.get_tracer("arango_memory")
_meter = otel_metrics.get_meter("arango_memory")

Handler = Callable[..., None]


class _Meters:
    """OTEL instruments mirroring the emitter events (DESIGN.md §18).

    Built once against the otel metrics **API**; if no `MeterProvider` is
    configured the instruments are no-ops, so dev/CI need no collector.
    """

    def __init__(self) -> None:
        self.writes = _meter.create_counter(
            "memory.writes", unit="1", description="Write-path completions"
        )
        self.write_duration = _meter.create_histogram(
            "memory.write.duration", unit="ms", description="Write latency"
        )
        self.retrievals = _meter.create_counter(
            "memory.retrievals", unit="1", description="Retrieve calls"
        )
        self.retrieval_duration = _meter.create_histogram(
            "memory.retrieval.duration", unit="ms", description="Retrieve latency"
        )
        self.retrieval_results = _meter.create_histogram(
            "memory.retrieval.results", unit="1", description="Hits returned per retrieve"
        )
        self.retrieval_tokens = _meter.create_histogram(
            "memory.retrieval.tokens", unit="1", description="Context tokens injected"
        )
        self.degraded = _meter.create_counter(
            "memory.degraded", unit="1", description="Degraded-mode fallbacks"
        )
        self.conflicts = _meter.create_counter(
            "memory.conflicts", unit="1", description="Entity conflicts detected"
        )
        self.decay_pruned = _meter.create_counter(
            "memory.decay.pruned", unit="1", description="Memories soft-deprecated by decay"
        )
        self.consolidations = _meter.create_counter(
            "memory.consolidations", unit="1", description="Dream-State consolidation runs"
        )
        self.consolidation_changes = _meter.create_counter(
            "memory.consolidation.changes",
            unit="1",
            description="Entities promoted/superseded/cleared by consolidation",
        )
        self.cache_lookups = _meter.create_counter(
            "memory.cache.lookups", unit="1", description="Enrichment cache lookups"
        )
        self.embedding_cache_lookups = _meter.create_counter(
            "memory.embedding_cache.lookups", unit="1", description="Embedding cache lookups"
        )

    def record(self, event: str, payload: dict[str, Any]) -> None:
        if event == "write":
            if payload.get("dead_lettered"):
                self.writes.add(1, {"outcome": "dead_lettered"})
            else:
                self.writes.add(1, {"outcome": "ok"})
            if (duration := payload.get("duration_ms")) is not None:
                self.write_duration.record(duration)
        elif event == "retrieval":
            attrs = {"mode": payload.get("mode", "unknown")}
            self.retrievals.add(1, attrs)
            if (duration := payload.get("duration_ms")) is not None:
                self.retrieval_duration.record(duration, attrs)
            if (results := payload.get("results_k")) is not None:
                self.retrieval_results.record(results, attrs)
            if (tokens := payload.get("tokens_injected")) is not None:
                self.retrieval_tokens.record(tokens, attrs)
        elif event == "degraded":
            self.degraded.add(
                1, {"op": payload.get("op", "unknown"), "reason": payload.get("reason", "unknown")}
            )
        elif event == "conflict":
            self.conflicts.add(int(payload.get("detected", 0)))
        elif event == "decay":
            self.decay_pruned.add(int(payload.get("pruned", 0)))
        elif event == "consolidation":
            self.consolidations.add(
                1, {"breaker_tripped": bool(payload.get("breaker_tripped", False))}
            )
            for kind in ("promoted", "superseded", "cleared"):
                if count := int(payload.get(kind, 0)):
                    self.consolidation_changes.add(count, {"kind": kind})
        elif event == "cache":
            self.cache_lookups.add(1, {"hit": bool(payload.get("hit", False))})
        elif event == "embedding_cache":
            self.embedding_cache_lookups.add(1, {"hit": bool(payload.get("hit", False))})


_meters = _Meters()


class MemoryMetrics:
    """Minimal synchronous event emitter feeding OTEL meters (DESIGN.md §18)."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def on(self, event: str, handler: Handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, **payload: Any) -> None:
        _meters.record(event, payload)
        for handler in self._handlers.get(event, ()):
            handler(**payload)

    def clear(self) -> None:
        """Drop all in-process handlers (used between tests). Meters are unaffected."""
        self._handlers.clear()


metrics = MemoryMetrics()


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    """OTEL span context manager; no-op without a configured provider."""
    with _tracer.start_as_current_span(name) as current:
        for key, value in attrs.items():
            current.set_attribute(key, value)
        yield current


__all__ = ["MemoryMetrics", "metrics", "span"]
