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
exclusive-locked `claim` prevents double-processing. Set **`REDIS_URL`** (the optional
shared layer, below) so the **rate limiter** enforces one global budget (not N×) and the
**embedding cache** is shared across instances. Without it those two are per-instance
(the limiter's effective cap is N×; caches run cold per process). The query (HyDE/gate)
cache and the `/health` latency window remain per-instance either way. Correlation ids
(`X-Request-ID`) thread requests across instances in your log pipeline.

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
open), `OIDC_ISSUER` (unset = JWT off; see Authentication below), `MAX_REQUEST_BYTES`
(1 MiB), `RATE_LIMIT_PER_MINUTE` (0 = off); scaling: `REDIS_URL` (unset = per-instance
limiter + cache; see Optional shared layer).

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

**OIDC / JWT (§17)** — for federated/SSO deployments, set `OIDC_ISSUER` to also accept
signed bearer **JWTs** from an external IdP (Auth0/Okta/Cognito/Keycloak/…). Coexists
with `API_KEYS` (a JWT is verified when the bearer is a JWT; otherwise it's matched
against the static keys), so you can migrate incrementally.

| Var | Default | Notes |
|---|---|---|
| `OIDC_ISSUER` | — | Set = enable JWT auth (also flips enforced mode) |
| `OIDC_AUDIENCE` | — | Expected `aud`; when set, `aud` is verified |
| `OIDC_JWKS_URI` | `{issuer}/.well-known/jwks.json` | Signing-key endpoint (cached, `kid`-rotated) |
| `OIDC_ALGORITHMS` | `["RS256"]` | Signature-alg allowlist (blocks `none`/HS confusion) |
| `OIDC_TENANT_CLAIM` | `tenant_id` | Claim mapped to the tenant |
| `OIDC_SCOPE_CLAIM` | `scope` | Claim mapped to read/write (`write` if it contains "write") |
| `OIDC_LEEWAY_SECONDS` | `60` | Clock-skew tolerance for `exp`/`nbf` |

Verification is **fail-closed** (a JWKS-fetch error is a `401`, never an open pass).
**Revocation is by expiry only** — there's no server-side denylist, so configure the IdP
for **short-lived tokens** (revocation latency = token TTL). **Rotation** is the IdP's
job (signing-key rotation is picked up via JWKS `kid`); you don't edit anything here.

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
`nack`) when queue throughput outgrows ArangoDB — roadmap, not built. (The write queue
stays ArangoDB-backed; `REDIS_URL` below is for the rate limiter + embedding cache only.)

---

## Optional shared layer (Redis)

By default the **rate limiter** and **embedding cache** are per-instance. Set
**`REDIS_URL`** (and install the extra: `pip install 'arango-memory[redis]'`) to share
them across instances — the only state that needs to be common when you scale out:

- **Rate limiter** → one **global** budget (atomic `INCR`+`EXPIRE`), so `RATE_LIMIT_PER_MINUTE`
  means what it says regardless of instance count (vs N× per-instance).
- **Embedding cache** → shared vectors (JSON, 30-day TTL), so a name embedded on one
  instance is reused by all (fewer paid embedding calls; new instances start warm).
  Per-tenant key namespacing (§24) is preserved.

**Fail-soft:** a Redis outage never breaks a request — the limiter **fails open**
(allows) and the cache **falls through** to a direct embed. Set Redis
`maxmemory-policy allkeys-lru` for a hard cache cap. Not shared: the query (HyDE/gate)
cache and the `/health` latency window (both still per-instance). The durable write
queue is unaffected (it's ArangoDB-backed).

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
  + `Retry-After`; `/health` is exempt. Per-instance by default (N instances → N× the
  cap); set **`REDIS_URL`** (below) for one shared cross-instance budget.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Retrieval returns hits but **no vector results** | The Faiss IVF index trains only once the corpus reaches `VECTOR_N_LISTS` docs (default 256); below that the read path is **BM25-only** by design and self-heals. Confirm with `python -m arango_memory.ops explain` / check `has_vector_index`. |
| `ERR 1554/1555 vector index not ready` on rebuild | Same threshold — the corpus is below `VECTOR_N_LISTS`. Wait for more data or lower `VECTOR_N_LISTS` for small deployments. |
| Connection refused / IPv6 weirdness on localhost | Use `ARANGO_URL=http://127.0.0.1:8529` (not `localhost`) to avoid IPv6 resolution issues. |
| `failed_writes` is growing | Writes are exhausting retries (bad data or a downstream outage). Inspect the collection, fix the cause, then `python -m arango_memory.ops replay`. |
| Writes accepted (`status:queued`) but never appear | With `WRITE_QUEUE_BACKEND=memory`, unacked work is **lost on crash** — set `WRITE_QUEUE_BACKEND=arango` in production. Also confirm the write worker thread is running (single process / not blocked). |
| Rate limit feels **N× too high** across instances | The in-process limiter is per-instance. Set `REDIS_URL` for one shared budget (see *Optional shared layer*). |
| `429`s with Redis down, or cache misses spike | Redis is fail-soft: the limiter **fails open** and the cache **falls through** to direct compute — check Redis reachability; behavior is by design, not an error. |
| Real embeddings/generation aren't happening | Defaults are **keyless fakes**. Set `EMBEDDING_PROVIDER=openai` (+ `OPENAI_API_KEY`) and/or `GENERATION_PROVIDER=anthropic` (+ `ANTHROPIC_API_KEY`); full-mode + Dream State distillation need a real generator. |
| `401` after enabling `API_KEYS`/`OIDC_ISSUER` | Enforced mode now requires `Authorization: Bearer <key|jwt>`; `/health` + `/docs` stay public. For JWT, verify `OIDC_AUDIENCE`/issuer match the token. |
| ArangoDB won't start / license prompt | The Enterprise image runs in **evaluation mode** without a license; set `ARANGO_LICENSE_KEY` for unrestricted use. |
| `/docs` or `/openapi.json` returns `401` | They shouldn't — they're auth-exempt. If you see this, a reverse proxy is likely enforcing auth ahead of the core. |

For anything write-durability or degradation related, see [§15](DESIGN.md) and the
**Durable write path** section above.

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
