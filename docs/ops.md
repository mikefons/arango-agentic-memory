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

**Container & probes.** The image (`core/Dockerfile`) is digest-pinned, runs as a
**non-root** user (uid 10001), and has a Docker `HEALTHCHECK` on `/health`. Two probes:
- **liveness → `GET /health`** — always `200` when the process is up (never gated on the
  DB), so a DB blip can't restart the pod.
- **readiness → `GET /ready`** — `200` when the DB is reachable, **`503`** when not, so
  the orchestrator stops routing traffic without restarting. Both are public (no auth).

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
(2), `CANDIDATE_POOL` (100 — per-arm candidates before fusion/rerank/MMR; on an open/large
corpus, raise it (e.g. 500) **with rerank** to recover the tail-reachable golds BX-3 found
(§23), at more per-query DB work; also settable per request via `opts.candidate_pool`),
`GRAPH_MAX_NEIGHBORS` (200 — SC-1c: caps the graph arm's `relates_to` fan-out so a dense
single-tenant graph can't blow up retrieval; the arm is a down-weighted expander so a bounded
neighbourhood costs little), `GRAPH_MAX_MEMORIES_PER_ENTITY` (50 — SC-1d: caps the memories
expanded per related entity, so a hub whose mentions grow as the tenant fills can't drive
retrieval latency unbounded; 50 leaves real corpora untouched and only caps pathological hubs),
`VECTOR_N_LISTS` (64), `VECTOR_TRAIN_FACTOR` (40 — index trains at
`n_lists × factor` docs),
`ENTITY_VECTOR_N_LISTS` (32) / `ENTITY_VECTOR_TRAIN_FACTOR` (40) / `ENTITY_RESOLUTION_TOP_K`
(10 — SC-1b: once a tenant accrues `n_lists × factor` entities, write-time resolution matches
a new entity against the **top-k nearest** via a Faiss index on `entities` instead of
full-scanning the tenant — keeps ingestion from going O(N²) as a long-lived tenant fills;
below the threshold it full-scans, which is fine at small N),
`MMR_LAMBDA` (1.0 — final re-rank relevance↔diversity;
1.0 = pure relevance/fusion order, lower trades recall for a more varied result set),
`RRF_GRAPH_WEIGHT` (0.1 — the graph arm expands recall but ranks by hop distance, not
query relevance, so it stays down-weighted), `RRF_VECTOR_WEIGHT` (1.0 — lower it if the
vector arm hurts on your corpus: it ranks by proximity to the *query*, which is only
relevance when the query resembles the answer; full mode's HyDE is the intended fix),
`ADAPTIVE_GATE` (`true` — full mode only:
spend an LLM call to skip retrieval when the turn needs no memory; set `false` when every
turn does, making full mode HyDE-only with no gate call),
`DECOMPOSE_MAX_SUBQUERIES` (4 — multihop mode (§9, RQ-1) only: cap on the sub-lookups a
query is split into; each adds one retrieval, so this bounds the fan-out. Latency is ~N×
a single retrieve plus one decompose LLM call — an augmented path, off the lite hot path),
`DECOMPOSE_MAX_HOPS` (0 — reserved for the iterative read→retrieve→read variant; off),
`RERANK_ENABLED` (`false` — cross-encoder rerank of the fused pool before MMR, RQ-2b; the
diagnosed fix for in-pool-but-unranked golds, §9/§23), `RERANKER_PROVIDER` (`fake` keyless /
`local` sentence-transformers — needs the `rerank` extra), `RERANKER_MODEL`
(`BAAI/bge-reranker-base`), `RERANK_TOP_N` (50 — how many top fused candidates to re-score;
cost scales with it. Off the lite hot path; degrades to the fused order if the model is
unavailable);
lifecycle: `DECAY_LAMBDA` (0.02),
`DECAY_FLOOR` (0.1),
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
           "k_acme_ro":{"tenant_id":"acme","scope":"read"},
           "k_researcher":{"tenant_id":"acme","scope":"write","agent_ids":["research-1","research::*"]},
           "k_curator":{"tenant_id":"acme","scope":"consolidate","agent_ids":["research::*"]}}'
```
Unset → **open** (body-trusted, the keyless default). Set → `/v1` requires
`Authorization: Bearer <key>` (`401` otherwise), and `tenant_id`/`access_level` are
taken from the key, not the request (`403` on tenant mismatch or read-key write).
`/health` stays public.

**Per-agent binding + scopes (MA-7).** A key may add `agent_ids` to restrict which
agents it can act as: a **write** with a `ctx.agent_id` outside the list is `403`, and
cross-agent **reads** are silently filtered to the allowed set (they degrade, never fail).
Entries support a glob-lite suffix (`"research::*"`) for a whole crew tier. Scope is now
ordered `read < write < consolidate`; writing an **`*::insight`** tier (consolidated
memory, normally Dream-State-only) requires **`consolidate`** scope — a plain `write` key
gets `403`. `agent_ids` omitted → any agent (the pre-MA-7 default; existing keys are
unchanged). **Rotation:** add the new key alongside the old, roll clients over, then drop
the old key. Keep keys in the host env / a gitignored `.env`, never in the image or VCS.

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
| `OIDC_SCOPE_CLAIM` | `scope` | Claim mapped to read/write/consolidate (highest match wins) |
| `OIDC_AGENT_CLAIM` | — | Optional (MA-7): claim whose value restricts the agents the token may act as (parity with a key's `agent_ids`) |
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

A Faiss IVF index trains lazily once the corpus reaches **`VECTOR_N_LISTS × VECTOR_TRAIN_FACTOR`**
docs (defaults 64 × 40 = 2 560), and the read path self-heals to BM25 until then. The
factor exists because IVF trains one centroid per list — building at exactly `n_lists`
docs gives ~one point per centroid, which returns garbage (the `n_lists ≪ corpus` rule).
Keep `VECTOR_N_LISTS` well below your expected corpus.

```bash
python -m arango_memory.ops vector-rebuild        # drop + recreate the index
python -m arango_memory.ops embeddings-migrate     # re-embed stale docs, then rebuild
python -m arango_memory.ops vector-diag            # probe the arm; print the RAW failure reason
```
Run `embeddings-migrate` after switching `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL`
(dimensions/version change). `purge` (hard delete) drops the index; it self-heals.
`vector-diag` runs a real retrieval and prints the exact `AQLQueryExecuteError` (not the
swallowed "retrieve degraded") plus the corpus/threshold/index-state — use it first when
the vector arm looks off.

**Changing `VECTOR_N_LISTS` needs a wipe or rebuild:** `ensure_vector_index` no-ops when an
index already exists, so a new `n_lists` only takes effect after `ops vector-rebuild` (or
`docker compose down -v`).

### Running the LoCoMo benchmark (§23)

Needs real embeddings (`EMBEDDING_PROVIDER=openai` + `OPENAI_API_KEY`) and the converted
dataset (`core/converted.json`, gitignored — build it with `locomo_convert`).

1. **One clean ArangoDB on 8529** — `docker ps | grep arango` should show exactly the
   root-compose container. `docker compose up` now raises `vm.max_map_count` for you.
2. **Start fresh so the index retrains at the current `n_lists`:** `docker compose down -v && up`.
3. `make benchmark DATASET=converted.json MODE=lite` (from `core/`).
4. **If it degrades:** `python -m arango_memory.ops vector-diag` prints the raw AQL error +
   corpus/threshold/index-state. With defaults the vector index trains at 2 560 docs, so a
   small run may legitimately stay BM25-only (`vector: deferred`) — lower `VECTOR_N_LISTS`
   **and** `VECTOR_TRAIN_FACTOR` together for a small-corpus vector run.

**Multi-hop mode (RQ-1).** `MODE=multihop` decomposes each question into sub-lookups for
the multi-hop category (§9). It needs a real generator (`GENERATION_PROVIDER=anthropic` +
`ANTHROPIC_API_KEY`) — with the `fake` generator, decomposition returns one lookup and it
falls back to single-shot. Cost per question is one decompose call + up to
`DECOMPOSE_MAX_SUBQUERIES`× the retrievals, so **smoke a subset first** (build a
multi-hop-only `converted.json`) and estimate token spend before the full 1531-Q run.

### Running the MuSiQue benchmark (BX-1, §23)

LoCoMo's multi-hop `gold_fact` is a single turn, so it can't test multi-hop *retrieval*
(DESIGN §23). [MuSiQue-Ans](https://github.com/StonyBrookNLP/musique) is genuinely
multi-evidence — use it to exercise the multi-evidence recall metric. Bring-your-own
(externally licensed, never committed).

1. Convert (JSONL → runner schema; `--limit` for a smoke subset):
   `python -m arango_memory.eval.musique_convert musique_ans_v1.0_dev.jsonl musique.json --limit 200`
2. Run (real embeddings; add `MODE=multihop` to also re-trial RQ-1 on data with headroom):
   `make benchmark DATASET=musique.json MODE=lite`

Each question becomes its own tenant with its ~20 candidate paragraphs (supporting +
distractors) as the corpus, faithful to MuSiQue's given-context setting. The report adds
**`recall-frac`** (graded mean fraction of the support set retrieved) next to the all-hops
`Recall@k`; read `recall-frac` as the primary multi-evidence signal.

**Pooled corpus (BX-2, open retrieval).** By default each MuSiQue question is its own
~20-paragraph tenant (given-context — every gold is trivially in the pool). Add `--pooled`
to the converter to merge all selected questions' **deduped** paragraphs into **one shared
tenant**, so each query retrieves against the whole corpus (thousands of docs) — the
open-retrieval stress test that can surface *first-stage-recall* misses:

```
python -m arango_memory.eval.musique_convert musique_ans_v1.0_dev.jsonl musique-pooled.json --limit 200 --pooled
python -m arango_memory.eval.pool_diag musique-pooled.json --pool 100 --lightweight   # look for RECALL (out-of-pool) misses
```

**Use `--lightweight` for a pooled `pool_diag` run (BX-3).** A full pooled corpus stalls
otherwise: per-`store()` entity resolution is ~O(n²) as the single tenant fills (a ~3k-doc
corpus took ~12 h to ingest), and the graph arm fans out and times out retrieval. First-stage
recall only needs BM25 + vector, so `--lightweight` ingests with **no entity extraction** and
probes with the **graph arm off** — completing in minutes. It routes *around* the scalability
limit, it does not fix it (DESIGN §23). Numbers here are **not** comparable to the per-question
runs — a different (harder) regime.

**Cross-encoder rerank (RQ-2b).** Add `RERANK=--rerank` to any run to re-rank the fused
pool with a cross-encoder before MMR — the fix for the ranking-bound misses RQ-2a found.
Needs the local reranker: install the extra and select it, then run:

```
uv pip install -e '.[rerank]'   # sentence-transformers + the model download on first use
RERANKER_PROVIDER=local make benchmark DATASET=musique.json MODE=lite RERANK=--rerank
```

Compare `recall-frac` / all-hops `Recall@k` against the un-reranked baseline on the same
dataset (the reranker is CPU-heavy — expect higher latency; it's off the lite hot path).

### Running the LongMemEval benchmark (HX-1, §23)

Where LoCoMo/MuSiQue score *retrieval* recall, LongMemEval scores **end-to-end answer
accuracy** — the metric the long-term-memory field reports. Bring-your-own
([longmemeval_s.json](https://github.com/xiaowu0162/LongMemEval), externally licensed, never
committed). Needs real embeddings **and** a real generator (the answerer + the LLM judge):
`EMBEDDING_PROVIDER=openai GENERATION_PROVIDER=anthropic` + keys.

1. Convert (JSONL/JSON → runner schema). LongMemEval-S is **grouped by question type**, so a
   plain `--limit N` returns a single category — use **`--stratified`** to sample `--limit`
   questions evenly across all six types for a representative first pass (it prints the type
   distribution):
   `python -m arango_memory.eval.longmemeval_convert longmemeval_s.json lme.json --stratified --limit 90`
2. Run (compose with `--rerank` / `MODE=multihop` as for the other benchmarks):
   `make longmemeval LME_DATASET=lme.json MODE=lite RERANK=--rerank`

**Ingestion skips entity extraction by default.** A LongMemEval history is hundreds of turns;
per-turn entity resolution over the growing tenant is ~O(n²) (the BX-2 wall) and dominates a
real run, while the graph adds ~nothing to *answer* accuracy — so extraction is off unless you
pass `--extract`. This is the difference between a many-hour run and a tractable one. Even so,
each question ingests its whole history with real embeddings (sequential API calls), so `--limit`
for the first pass. Set `EXTRACTION_PROVIDER=fake` and `MEMORY_MODE=lite` (the defaults) — a real
extractor or `full` mode adds an LLM call *per turn* and will make the run intractable.

Each question becomes its own tenant (its evidence + distractor sessions as the corpus). The
report is **Accuracy** overall + per `question_type`, plus a **correct-decline rate** over the
abstention (`_abs`) questions. `--min-accuracy X` makes the run exit nonzero below a gate.
Note the accuracy partly reflects the answerer/judge model, so record which model was used and
read it alongside the LoCoMo/MuSiQue retrieval-recall numbers (which isolate the memory layer).
With `RERANKER_PROVIDER=fake` the run uses the keyless token-overlap stand-in (CI/plumbing
only, not a real quality signal).

### Retrieval-miss diagnostic (RQ-2a)

Splits recall misses into **ranking** (gold is in the fused pool but below top-k → a
reranker helps) vs **recall** (gold absent from the pool → first-stage retrieval must
improve). Read-only, no LLM calls; run it on the same converted dataset:

```
python -m arango_memory.eval.pool_diag musique.json --k 10 --pool 100
```

It prints the overall + per-category `ranking / recall` split of the misses and names the
implied RQ-2b lever (reranker vs query expansion). Point it at MuSiQue (which has real
retrieval headroom); on LoCoMo the split is less informative.

### Scaling profiler (SC-1a)

Measures how `store()` and `retrieve()` latency grow as **one tenant** fills, to confirm the
O(N²) ingestion curve (entity resolution full-scans the tenant's entities per write — no
vector index on `entities`) and baseline the SC-1b fix:

```
python -m arango_memory.eval.scaling_profile --max 3000 --step 500 --probes 20
```

Prints a `size / store_p50 / store_p99 / retrieve_p50 / retrieve_p99` table plus the
store-latency growth factor. **It is slow by design** — it's measuring the very slowdown, so
per-`store()` climbs as the tenant grows. Run it against a DB whose vector-index dimension
matches your embedder (the persisted index is built at first ingest: 1536 for `openai`, 256
for `fake` — mixing them errors); `docker compose down -v && up` for a clean run. After SC-1b
the `store_p50` column should stay roughly flat instead of climbing.

---

## Observability (§18)

- **`GET /ready`** — readiness: `200` when the DB is reachable, `503` when not (wire the
  orchestrator's readiness probe here; `/health` stays liveness-only). Public.
- **`GET /health`** — liveness + DB reachability + vector-arm state + process-global
  latency (`{status, arango, vector, mode, latency_ms}`). `vector` is `trained` /
  `deferred` (corpus below the training threshold → BM25-only) / `unknown` (DB
  unreachable). `latency_ms` holds p50/p95/p99 (ms) over
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
- **Embeddings** are never returned over the API (inversion defense). They are stored as
  plain vectors — **encryption at rest is a storage-layer responsibility** (the app can't
  encrypt the field without breaking vector search). For sensitive corpora, enable
  **ArangoDB Enterprise encryption-at-rest** (`--rocksdb.encryption-keyfile`) or run on an
  encrypted disk/volume; the per-tenant cache namespacing + never-returned guarantees are
  the app-level defenses (§17).
- **Abuse limits** — `MAX_REQUEST_BYTES` (default 1 MiB, always on) rejects oversized
  bodies with **`413`** before they're buffered. `RATE_LIMIT_PER_MINUTE` (`0` = off)
  throttles per **tenant** (authenticated) or **client IP** (open mode) with **`429`**
  + `Retry-After`; `/health` is exempt. Per-instance by default (N instances → N× the
  cap); set **`REDIS_URL`** (below) for one shared cross-instance budget.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Retrieval returns hits but **no vector results** | The Faiss IVF index trains only once the corpus reaches `VECTOR_N_LISTS × VECTOR_TRAIN_FACTOR` docs (default 64 × 40 = 2 560); below that the read path is **BM25-only** by design and self-heals. Check `GET /health` → `vector: deferred`, or `python -m arango_memory.ops vector-diag`. |
| `ERR 1554/1555 vector index not ready` on rebuild | Corpus below the threshold. Wait for more data, or lower `VECTOR_N_LISTS` **and** `VECTOR_TRAIN_FACTOR` for small deployments — then `ops vector-rebuild` (a plain restart won't rebuild an existing index). |
| Persistent **"retrieve degraded"** with no reason | Run `python -m arango_memory.ops vector-diag` — it prints the raw `AQLQueryExecuteError`. The CLIs now call `configure_logging()`, so `LOG_FORMAT=json` also surfaces the `detail` field. A common cause is an under-trained index built at the old `n_lists`-only threshold; `ops vector-rebuild`. |
| arangod **crashes during index build** / `Connection refused` mid-run | `vm.max_map_count` too low for the Faiss mmap. `docker compose up` now raises it via the `sysctl-init` service; on a rootless/podman host set it manually: `sudo sysctl -w vm.max_map_count=1048576`. |
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
