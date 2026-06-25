# ArangoDB Agentic Memory

Persistent, relational memory for AI agents — built on ArangoDB (graph + vector + full-text in one engine), with a Python core and a thin Vercel AI SDK adapter.

> **Status:** Feature-complete against the spec and hardened into a deployable
> service — v1 core (Steps 0–7), all v2 adapters (MCP, LangChain/LangGraph, CrewAI),
> the full entity API, the GLiNER/Haiku extraction tier, the **Memory Dungeon**
> reference app (`examples/dungeon/`), plus the hardening tracks: bearer-key **+
> JWT/OIDC auth**, durable write queue, rate limiting, structured logging,
> OpenAPI `/docs`, a gated release pipeline, and an optional **Redis** shared layer
> for horizontal scaling. See [`docs/DESIGN.md`](docs/DESIGN.md) — the authoritative
> spec — and [`docs/api.md`](docs/api.md) — the core API reference..

## Architecture (v1)

```
┌─────────────────────┐     HTTP       ┌──────────────────────────┐     AQL     ┌───────────┐
│  Vercel AI SDK app  │ ─────────────▶ │   Python core (FastAPI)  │ ──────────▶ │ ArangoDB  │
│  @arango-memory/    │  /v1/store     │  ingest · retrieve ·     │             │ graph +   │
│  vercel middleware  │  /v1/retrieve  │  lifecycle · security ·  │             │ vector +  │
│  (LanguageModelV2)  │  /v1/step …    │  telemetry               │             │ BM25      │
└─────────────────────┘                └──────────────────────────┘             └───────────┘
```

- **Python-first core** (`core/`) — all memory intelligence: schema, ingestion, retrieval, lifecycle, security, telemetry.
- **Thin TypeScript client** (`packages/vercel/`) — a `LanguageModelV2Middleware` (`ai@5`) that retrieves-and-injects before a turn, durably stores after, and captures tool calls as procedural memory. No memory logic of its own.
- **Reference app** (`examples/vercel-agent/`) — a minimal real `generateText` loop wiring the adapter to the core.

v1 ships the core + Vercel adapter. v2 adds three in-process adapters (the core API is adapter-neutral, so they're additive): an **MCP server** (`arango_memory/mcp/`, 9 tools), a **LangChain/LangGraph adapter** (`arango_memory/langchain/` — `ArangoMemoryRetriever`, `ArangoChatMessageHistory`, `ArangoMemoryNode`), and a **CrewAI adapter** (`arango_memory/crewai/` — `ArangoCrewStorage` + `crew_memory()` G-Memory 3-tier + `to_crewai_storage()` shim).

## What's implemented

- **Ingestion** — PII redaction (§17), pluggable entity + **typed-relation** extraction → entity/edge knowledge graph (spaCy / GLiNER+GLiREL / Haiku tiers, layered with escalation), explicit `valid_time` parsing, write-time conflict detection, prospective indexing (full mode), idempotency-keyed **durable async write path** (queue + worker + dead-letter).
- **Retrieval** — BM25 + Faiss vector + graph expansion, fused via RRF → MMR → tiered token budget; HyDE + adaptive gate (full mode); recency/decay ranking. Degrades to a memory-less turn on any fault (never breaks the agent).
- **Lifecycle** — Ebbinghaus decay + spaced repetition + soft-deprecation sweep; bi-temporal edges + `Supersedes`; Dream State consolidation (conflict confirmation + summary distillation + circuit breaker).
- **Security** — PII redaction, WORM episodes, right-to-be-forgotten (soft-delete + purge), ABAC (read/write); **authentication** (open by default) via static bearer keys *or* OIDC/JWT (claims → tenant/scope), with per-tenant **rate limiting** + request-size caps.
- **Observability** — OpenTelemetry spans **+ `memory.*` meters** (no-op without a configured backend) + a `MemoryMetrics` event emitter; **structured JSON/text logging** with `X-Request-ID` correlation; in-process p50/p95/p99 **latency percentiles on `/health`**; `GET /v1/stats`. Sample collector + Grafana dashboard in `deploy/observability/`.
- **Operations** — idempotency-keyed durable write path with a pluggable queue (in-memory or **ArangoDB-backed**) + dead-letter/replay; an `ops` CLI (`vector-rebuild`, `embeddings-migrate`, `replay`, `explain`); stateless API → **multi-instance** scaling, with an optional **Redis** shared layer (`REDIS_URL`) for a cross-instance rate-limit budget + shared embedding cache.

HTTP surface: `/health`, **`/docs`** (OpenAPI), `/v1/store`, `/v1/retrieve`, `/v1/step`, `/v1/steps`, `/v1/forget`, `/v1/stats`, `/v1/entity`, `/v1/entities`, `/v1/graph`, `/v1/seed`, `/v1/supersede`, `/v1/dream`, `/v1/salience`, `/v1/community`, `/v1/ontology/*`.

Deferred: full Next.js chat UI (Step 3.5c).

## Quick start (local dev)

```bash
cp .env.example .env          # OPENAI_API_KEY, ANTHROPIC_API_KEY, ARANGO_LICENSE_KEY
docker compose up -d          # ArangoDB (Enterprise) + Python core sidecar
# core API: http://localhost:8080  ·  ArangoDB UI: http://localhost:8529
```

> Uses the ArangoDB **Enterprise** image (`arangodb/enterprise:3.12.9.1`) for
> vector-index auto-training. Set `ARANGO_LICENSE_KEY` in `.env`; it runs in
> evaluation mode without one. Defaults are keyless: extraction/generation use
> deterministic fakes unless you configure `openai`/`anthropic` providers.

### Secret-scanning hook (one-time per clone)

A gitleaks pre-commit hook blocks commits containing hardcoded secrets.

```bash
brew install gitleaks pre-commit   # or: pip install pre-commit
pre-commit install                 # activates the hook for this clone
```

Manual scan: `gitleaks dir . --redact`

### Core (Python, uv)

```bash
cd core
make sync     # install deps into the relocated venv
make dev      # run the core API with autoreload on :8080
make ci       # lint (ruff) + type (mypy --strict) + test (pytest/testcontainers)
```

See [`core/README.md`](core/README.md) for the Makefile rationale and connection targets.

### Vercel adapter (TypeScript, npm)

```bash
cd packages/vercel
npm install
npm run typecheck && npm run build && npm test
```

### Reference agent

```bash
cd examples/vercel-agent   # see its README: needs the core running + an Anthropic key
```

## Testing & CI

Two CI jobs run on every push/PR:
- **Core** — `make ci`: ruff + mypy --strict + pytest. Integration tests spin a real ArangoDB Enterprise container via **testcontainers** (evaluation mode, no license); deterministic and keyless (fake embedder/generator/extractor).
- **Adapter** — `npm` typecheck + build + vitest.

Plus a **deterministic simulation harness** (`core/src/arango_memory/sim/`) that drives the core's HTTP surface through a multi-session agent loop with tool calls — the real-data regression gate for memory *and* actions.

## Repository layout

```
docs/DESIGN.md         Authoritative design spec
docs/api.md            Core API reference (/v1 HTTP + in-process Python)
docs/ops.md            Operations runbook (run · config · jobs · security)
docs/adapters/         Per-adapter guides (vercel · langchain · crewai · mcp)
core/                  Python core (uv): ingest · retrieve · lifecycle ·
                       security · telemetry · sim · eval
packages/vercel/       Thin TS client middleware (npm) + vitest
examples/vercel-agent/ Minimal reference agent (adapter → core → ArangoDB)
docker-compose.yml     ArangoDB (Enterprise) + core for local dev
.github/workflows/     CI (core + adapter) + gitleaks
```

## License

[MIT](LICENSE).
