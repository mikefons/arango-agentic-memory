# arango-memory (core)

The Python core of the ArangoDB Agentic Memory system. Houses all memory
intelligence: schema management, ingestion, retrieval, consolidation, and decay.
Exposes a language-agnostic API consumed in-process (Python adapters) and over
HTTP (the Vercel TS adapter).

See [`../docs/DESIGN.md`](../docs/DESIGN.md) for the authoritative spec.

## Layout

```
src/arango_memory/
  client.py       ArangoDB client + connection abstraction
  config.py       Settings (env-driven)
  embedding.py    Pluggable embedder (fake / OpenAI)
  generation.py   Pluggable generator (fake / Anthropic)
  stats.py        Per-tenant graph health counts
  schema/         Collection / view / index definitions
  ingest/         Extraction, conflict detection, prospective, durable write queue + worker
  retrieve/       HyDE, hybrid search (BM25 + vector + graph), RRF/MMR, token budget
  lifecycle/      Decay, bi-temporal/Supersedes, Dream State consolidation
  security/       PII redaction, WORM, right-to-be-forgotten
  telemetry/      OpenTelemetry spans + MemoryMetrics emitter
  api/            FastAPI service — the boundary (/v1/*)
  eval/           LoCoMo-style eval runner
  sim/            Deterministic agentic simulation harness
```

## Develop

Use the `Makefile` — it bakes in the two env settings every command needs
(relocated venv + `PYTHONPATH=src`), so you don't have to remember them:

```bash
make sync     # install/update deps (into ~/.venvs/arango-memory)
make dev      # run the core API with autoreload on :8080
make check    # connection/round-trip probe
make test     # pytest
make ci       # lint + type + test
```

For ad-hoc `uv` commands outside make, export the same venv path first:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/arango-memory"
uv run --no-sync pytest
```

> **Why the relocated venv:** if this repo lives under an iCloud-synced
> `Documents` folder, iCloud creates `name 2.ext` conflict copies inside the
> virtualenv, which corrupt packages (e.g. a duplicate
> `tiktoken_ext/openai_public 2.py` breaks tiktoken). Keeping the venv at
> `$HOME/.venvs/arango-memory` (outside the synced tree) avoids this entirely.
> If you ever see corruption: `make clean-venv`.

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
