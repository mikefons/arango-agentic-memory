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
# PYTHONPATH=src makes the package importable regardless of editable-install
# state (uv's src-layout editable .pth can be flaky across re-syncs).
PYTHONPATH=src uv run uvicorn arango_memory.api.app:app --reload --port 8080
uv run pytest                       # pythonpath=src configured in pyproject
uv run ruff check . && uv run mypy src
```

> **iCloud caveat:** if this repo lives under an iCloud-synced `Documents`
> folder, iCloud creates `name 2.ext` conflict copies inside `.venv`, which can
> corrupt packages (e.g. a duplicate `tiktoken_ext/openai_public 2.py` breaks
> tiktoken). Keep the venv out of the synced tree:
> `export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/arango-memory"` before `uv sync`.
> Quick repair if it happens: `find .venv -name '* [0-9].*' -delete` or rebuild
> with `rm -rf .venv && uv sync --extra dev`.

## Connection targets (local Docker vs ArangoGraph)

The core connects to either a local Docker container or ArangoGraph (Arango's
managed cloud), selected entirely via environment. Verify connectivity with a
real store→retrieve probe:

```bash
# local Docker (default)
PYTHONPATH=src uv run python -m arango_memory.check

# ArangoGraph
ARANGO_TARGET=arangograph \
ARANGO_URL=https://<deployment-id>.arangodb.cloud:8529 \
ARANGO_PASSWORD=<root-password> \
PYTHONPATH=src uv run python -m arango_memory.check
```

The probe connects, bootstraps the schema, stores and retrieves under an
isolated `__healthcheck__` tenant, then cleans up. See `.env.example` for the
full ArangoGraph variable set.

> Note: ArangoGraph is a managed service, so the `--vector-index` startup flag
> isn't user-settable there. The BM25 path works today; confirm vector-index
> availability on your ArangoGraph tier before relying on it (DESIGN.md §7).
