# Sample observability stack

A runnable starting point for exporting the core's OpenTelemetry signals
(DESIGN.md §18). **Sample/reference config — not production-tuned.** The core emits
spans + `memory.*` meters through the OTEL *API*; they are no-ops until a provider
exports them, so the flow is:

```
core ──OTLP──▶ otel-collector ──▶ Prometheus (metrics) ──▶ Grafana
                              └──▶ stdout (traces; swap for Tempo/Jaeger)
```

## Run it

```bash
# 1. Start collector + Prometheus + Grafana
docker compose -f docker-compose.observability.yml up

# 2. Run the core with OTLP export (zero-code via the auto-instrumentation distro)
pip install opentelemetry-distro opentelemetry-exporter-otlp
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
OTEL_SERVICE_NAME=arango-memory \
opentelemetry-instrument uvicorn arango_memory.api.app:app --port 8080

# 3. Grafana → http://localhost:3000 (anonymous admin). Add a Prometheus
#    datasource at http://prometheus:9090, then import grafana-dashboard.json.
```

Without a collector you still get **in-process p50/p95/p99 on `GET /health`**
(`latency_ms`) — see ops.md; this stack is for fleet-wide dashboards/alerting.

## Files

| File | Purpose |
|---|---|
| `otel-collector-config.yaml` | OTLP in → Prometheus exporter (`:8889`) + trace debug |
| `prometheus.yml` | Scrapes the collector's `:8889` |
| `grafana-dashboard.json` | Retrieval p99 (vs §23), throughput, degraded rate, cache hit ratio |
| `docker-compose.observability.yml` | Wires the three together |

## Metric names

The OTEL→Prometheus exporter normalizes instrument names: dots → underscores,
counters gain `_total`, histograms expand to `_bucket`/`_sum`/`_count`, and the unit
is appended (so `memory.retrieval.duration` (ms) → `memory_retrieval_duration_milliseconds_*`).
The dashboard's PromQL uses those normalized names; exact suffixes can vary by
collector version, so adjust if a panel reads empty (check the collector's `:8889`).
