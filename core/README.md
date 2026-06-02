# arango-memory (core)

The Python core of the ArangoDB Agentic Memory system. Houses all memory
intelligence: schema management, ingestion, retrieval, consolidation, and decay.
Exposes a language-agnostic API consumed in-process (Python adapters) and over
HTTP (the Vercel TS adapter).

See [`../docs/DESIGN.md`](../docs/DESIGN.md) for the authoritative spec.

## Layout

```
src/arango_memory/
  client.py     ArangoDB client + connection abstraction
  config.py     Settings (env-driven)
  schema/       Collection / view / index definitions + migrations
  ingest/       PII redaction, extraction, prospective indexing, writes
  retrieve/     HyDE, hybrid search, fusion, reranking, token budget
  lifecycle/    Decay, consolidation, Dream State worker
  api/          FastAPI service — the boundary (/v1/*)
  telemetry/    OpenTelemetry instrumentation
```

## Develop

```bash
uv sync --extra dev
uv run uvicorn arango_memory.api.app:app --reload --port 8080
uv run pytest
uv run ruff check . && uv run mypy src
```
