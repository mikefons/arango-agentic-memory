"""Observability (DESIGN.md §18): OTEL spans + an in-process metrics emitter.

Two backends, both opt-in:
  - `span(name, **attrs)` — an OpenTelemetry span via the otel **API**, which is
    a no-op unless the user configures an SDK/exporter (so CI needs no collector).
  - `metrics` — a `MemoryMetrics` event emitter for programmatic callbacks
    (`metrics.on("retrieval", handler)`); emits carry the cost/health payloads.

No built-in dashboard — users wire their own backend. OTEL metric *instruments*
(meter histograms/counters) are an additive follow-on; span attributes + emitter
payloads carry the values today.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span

_tracer = trace.get_tracer("arango_memory")

Handler = Callable[..., None]


class MemoryMetrics:
    """Minimal synchronous event emitter (DESIGN.md §18)."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def on(self, event: str, handler: Handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, **payload: Any) -> None:
        for handler in self._handlers.get(event, ()):
            handler(**payload)

    def clear(self) -> None:
        """Drop all handlers (used between tests)."""
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
