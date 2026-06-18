# Operations Runbook

How to run, configure, and maintain the memory **core** (the long-lived FastAPI
service + ArangoDB). See [`api.md`](api.md) for the request contract and
[`DESIGN.md`](DESIGN.md) for architecture.

---

## Run targets

- **Local dev** — `examples/dungeon/docker-compose.yml` brings up ArangoDB
  Enterprise (eval mode, no license) + the core:
  ```bash
  docker compose up --build      # arango :8529, core :8080
  ```
  The core image is `core/Dockerfile` (uv-based). Rebuild it (`--build`) whenever
  the Python core changes — a `git pull` alone won't update a running image.
- **Production** — the core is **long-lived** (durable write-worker thread +
  persistent DB connection), so run it as a **container on a long-running host**
  (Fly.io / Railway / Render / a VM), not on serverless. Point it at managed
  **ArangoGraph** (or your own ArangoDB) via the `ARANGO_*` env vars. Front it with
  the consumer app (e.g. Next.js on Vercel) over the `/v1` HTTP boundary.

`uvicorn arango_memory.api.app:app --host 0.0.0.0 --port 8080` is the entrypoint
(the module-level `app = create_app()` boots the schema + write worker on startup).

**Multiple instances.** The API is stateless, so scale out by running N instances
against the **same** ArangoDB — required: `WRITE_QUEUE_BACKEND=arango` (the in-memory
queue is per-process), so every instance's worker shares one durable backlog and the
exclusive-locked `claim` prevents double-processing. The in-process caches (query +
embedding) and the rate limiter are **per-instance** (they run cold per process; the
effective rate limit is N×) — a shared Redis layer for cross-instance caching + rate
limiting is on the roadmap. Correlation ids (`X-Request-ID`) thread requests across
instances in your log pipeline.

---

## Configuration (environment)

Settings are env-driven (pydantic-settings; var = field name, case-insensitive;
also read from a `.env`). Defaults are **keyless** (deterministic fakes) so dev/CI
need no API keys.

**Connection**
| Var | Default | Notes |
|---|---|---|
| `ARANGO_URL` | `http://localhost:8529` | Use `127.0.0.1` locally to avoid IPv6 issues |
| `ARANGO_DB` | `agentic_memory` | Created lazily on first connect |
| `ARANGO_USERNAME` / `ARANGO_PASSWORD` | `root` / `changeme` | Basic auth |
| `ARANGO_BEARER_TOKEN` | — | ArangoGraph token (overrides basic auth) |
| `ARANGO_TLS_VERIFY` | `true` | `true`/CA-path for HTTPS; ignored for http |
| `ARANGO_TARGET` | `local` | `local` \| `arangograph` (informational) |

**Providers** (all default `fake` → keyless)
| Var | Default | Real option |
|---|---|---|
| `EMBEDDING_PROVIDER` (+ `OPENAI_API_KEY`, `EMBEDDING_MODEL`) | `fake` | `openai` |
| `GENERATION_PROVIDER` (+ `ANTHROPIC_API_KEY`, `BACKGROUND_MODEL`) | `fake` | `anthropic` (Haiku) |
| `EXTRACTION_PROVIDER` | `fake` | `spacy` \| `gliner` \| `haiku` \| `layered` (need the `extraction` extra) |
| `MEMORY_MODE` | `lite` | `full` (adds HyDE + adaptive gate + prospective indexing) |

**Behavior knobs** — retrieval: `MAX_MEMORY_TOKENS` (1500), `K` (10), `GRAPH_HOPS`
(2), `VECTOR_N_LISTS` (256); lifecycle: `DECAY_LAMBDA` (0.02), `DECAY_FLOOR` (0.1),
`CONSOLIDATION_MENTION_THRESHOLD` (5), `DREAM_BREAKER_THRESHOLD` (0.5),
`CORROBORATION_BASE` (0.5), `ONTOLOGY_EVOLUTION` (`false`),
`ONTOLOGY_MIN_SUPPORT` (3); working memory: `WORKING_SESSION_TTL_SECONDS` (3600),
`WORKING_CAPACITY` (7), `TOPIC_SHIFT_THRESHOLD` (0.7), `TOPIC_EWA_ALPHA` (0.5),
`WEIGHT_EWA_ALPHA` (0.5), `WEIGHT_LAMBDA` (0.02); embedding cache:
`EMBEDDING_CACHE` (`true`), `EMBEDDING_CACHE_SIZE` (10000); conflict: `ENTITY_MERGE_THRESHOLD` (0.9),
`ENTITY_FLAG_THRESHOLD` (0.6); durable writes: `WRITE_MAX_RETRIES` (5),
`WRITE_BACKOFF_BASE` (0.5), `WRITE_QUEUE_BACKEND` (`memory`; set `arango` in prod),
`WRITE_LEASE_SECONDS` (60); security: `REDACT_PII` (`true`), `API_KEYS` (unset = auth
open), `MAX_REQUEST_BYTES` (1 MiB), `RATE_LIMIT_PER_MINUTE` (0 = off).

**Authentication (§17)** — bearer API keys. Set `API_KEYS` to a JSON map to enforce:
```bash
API_KEYS='{"k_acme_live":{"tenant_id":"acme","scope":"write"},
           "k_acme_ro":{"tenant_id":"acme","scope":"read"}}'
```
Unset → **open** (body-trusted, the keyless default). Set → `/v1` requires
`Authorization: Bearer <key>` (`401` otherwise), and `tenant_id`/`access_level` are
taken from the key, not the request (`403` on tenant mismatch or read-key write).
`/health` stays public. **Rotation:** add the new key alongside the old, roll
clients over, then drop the old key (both are valid while both are listed). Keep
keys in the host env / a gitignored `.env`, never in the image or VCS.

> **Secrets:** never commit keys. Use the host's environment or a gitignored
> `.env`. The repo's gitleaks hook + CI secret scan guard against leaks.

---

## Durable write path & dead letters (§15)

`/v1/store` and `/v1/step` enqueue idempotency-keyed intents; a worker **claims** one,
commits with exponential backoff (`WRITE_MAX_RETRIES` / `WRITE_BACKOFF_BASE`), then
**acks** it. Persistent failures land in the **`failed_writes`** collection
(dead-letter). Idempotency keys make retries/replays safe — they can't duplicate.

```bash
python -m arango_memory.ops replay      # re-enqueue + commit dead-lettered writes
```

### Queue backend (`WRITE_QUEUE_BACKEND`)
- **`memory`** (default) — in-process; fast and zero-config for dev/CI. Intents that
  are enqueued but not yet committed are **lost if the process dies** (the client
  already got `{status:queued}`). Fine for a single-instance demo.
- **`arango`** — **set this in production.** Intents persist to a `write_intents`
  collection; `claim` leases an intent (`WRITE_LEASE_SECONDS`, default 60) and `ack`
  deletes it only after commit, so a crash between accept and commit **redelivers**
  after the lease expires (at-least-once; idempotency keys dedupe). Survives restarts.

**Multi-instance:** run N stateless API instances + worker(s) over the **same**
`arango` queue + DB; the exclusive-locked `claim` prevents two workers taking the
same intent (the in-process caches just run cold per instance). The `memory` backend
is single-process only.

**Redis/SQS** slot in behind the same `WriteQueue` protocol (`enqueue`/`claim`/`ack`/
`nack`) when queue throughput outgrows ArangoDB — roadmap, not built.

---

## Scheduled / maintenance jobs

These are **callable passes**, not background timers — schedule them (cron, a
Vercel Cron hitting the endpoint, or an ops job):

| Job | Trigger | Effect |
|---|---|---|
| **Decay sweep** (§11) | `arango_memory.lifecycle.decay.decay_sweep(db, tenant_id=…)` | Soft-deprecates memories below `DECAY_FLOOR` |
| **Dream State** (§13) | `POST /v1/dream` or `run_dream_state(...)` | Conflict confirm → supersede, distillation; circuit breaker |
| **Salience** (§9) | `POST /v1/salience` or `compute_centrality(...)` | Recompute PageRank `centrality` |
| **Community** (§9/§13) | `POST /v1/community` or `compute_communities(...)` | Recompute label-propagation `community` labels (scopes Dream State) |
| **Ontology scan** (§13, flag) | `POST /v1/ontology/scan` or `propose_relationship_types(...)` | Propose typed relationships from `associated_with` clusters (needs `ONTOLOGY_EVOLUTION=true` + a real generator; human approves) |

Background LLM work (Dream State distillation) needs a real generator —
set `GENERATION_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` or it no-ops on the fake.

---

## Vector index (§7)

A Faiss IVF index trains lazily once the corpus reaches `VECTOR_N_LISTS` docs
(ArangoDB errors below that), and the read path self-heals to BM25 until then.

```bash
python -m arango_memory.ops vector-rebuild        # drop + recreate the index
python -m arango_memory.ops embeddings-migrate     # re-embed stale docs, then rebuild
```
Run `embeddings-migrate` after switching `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL`
(dimensions/version change). `purge` (hard delete) drops the index; it self-heals.

---

## Observability (§18)

- **`GET /health`** — liveness + DB reachability + process-global latency
  (`{status, arango, mode, latency_ms}`). `latency_ms` holds p50/p95/p99 (ms) over
  a rolling window per operation (`retrieval.lite`, `retrieval.full`, `write`),
  for checking tail latency against the §23 targets (**core retrieval p99 ≤ 200ms;
  lite end-to-end ≤ 250ms**) without an OTEL exporter. Empty until traffic flows;
  it's a rolling **in-process** window, so it's per-instance.
- **`GET /v1/stats?tenant_id=`** — per-tenant collection counts.
- **OTEL spans** — emitted around `memory.write` / `memory.retrieve`; **no-op
  unless** an OpenTelemetry provider is configured in the host process.
- **OTEL meters** — counters + histograms under the `memory.*` namespace
  (`memory.writes`, `memory.retrievals` + `memory.retrieval.duration`/`.results`/
  `.tokens` histograms, `memory.degraded`, `memory.conflicts`, `memory.decay.pruned`,
  `memory.consolidations`, `memory.cache.lookups`, `memory.embedding_cache.lookups`).
  Recorded automatically from the
  emitter; export them by configuring a `MeterProvider` (e.g. the Prometheus or OTLP
  exporter) in the host process — no-op otherwise. A runnable sample collector +
  Prometheus + Grafana dashboard lives in
  [`deploy/observability/`](../deploy/observability/README.md).
- **`MemoryMetrics`** event emitter — `retrieval`/`write`/`degraded`/`decay`/
  `consolidation`/`conflict`/`cache`/`embedding_cache`/`topic_shift`/`graph` events;
  subscribe in-process.
- **Structured logs** (§18) — stdlib logging on the `arango_memory` logger:
  `LOG_FORMAT=text` (human, dev/CI default) or `json` (one object/line for log
  pipelines), at `LOG_LEVEL` (INFO). Every line carries a **`request_id`**
  (correlation id) + **`tenant`**. A `RequestLogMiddleware` assigns/echoes
  `X-Request-ID` (honoring an inbound one) and logs an access line per request
  (method/path/status/`duration_ms`); the worker's **dead-letter** and **degraded
  retrieve** paths log too, sharing the id. `/health` isn't access-logged.

---

## Security ops (§17)

- **PII redaction** runs at ingestion before persist (`REDACT_PII=true`); the
  original is never stored.
- **Right to be forgotten** — `POST /v1/forget` (soft-delete). **Hard delete** is
  ops-only: `from arango_memory.security.forget import purge; purge(db, tenant_id=…)`
  (removes vertices + touching edges, episodes via the sanctioned WORM bypass,
  drops the vector index to self-heal).
- **ABAC** — mutating endpoints require `access_level: "write"` (see `api.md`).
- **Embeddings** are never returned over the API (inversion defense).
- **Abuse limits** — `MAX_REQUEST_BYTES` (default 1 MiB, always on) rejects oversized
  bodies with **`413`** before they're buffered. `RATE_LIMIT_PER_MINUTE` (`0` = off)
  throttles per **tenant** (authenticated) or **client IP** (open mode) with **`429`**
  + `Retry-After`; `/health` is exempt. The limiter is **per-instance** (in-process) —
  with N instances the effective limit is N×; a shared (Redis) limiter for a true
  cross-instance cap is on the roadmap.

---

## Schema & upgrades

`ensure_schema(db)` runs at startup: it creates the baseline collections/indexes
and applies registered migrations (`schema/migrations.py`; `MIGRATIONS` is empty at
v1 — future schema changes register a `Migration`). Back up with
`arangodump` / restore with `arangorestore` per ArangoDB's standard tooling.

### Index audit (§6)

Every hot-path query scopes a collection by some prefix of
`(tenant_id, agent_id, invalid_at)` (or a collection-specific key), so each is
backed by a persistent index created in `ensure_schema`: `idx_mem_scope`
(memories), `idx_entity_scope` (entities), `idx_episode_session` (episodes),
`idx_intent_lease` (write_intents), `idx_proposal_scope` (ontology_proposals) —
alongside the unique natural-key/idempotency indexes. The BM25 arm is served by
the ArangoSearch view (`tenant_id`/`agent_id` are indexed view fields).

Verify the planner actually uses them against a live DB:
```bash
python -m arango_memory.ops explain      # ⚠ flags any hot query falling back to a full scan
```
