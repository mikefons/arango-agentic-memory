# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once `1.0.0`
ships. Until then (`0.x`), minor versions may carry breaking changes; see
[Versioning](docs/DESIGN.md#25-development-tooling--infrastructure).

The Python core (`arango-memory`) and the Vercel adapter (`@arango-memory/vercel`)
are versioned together and released from this repository.

## [Unreleased]

### Added

- The core logs every database call that fails, and every one that takes
  `ARANGO_SLOW_CALL_SECONDS` (default 5, 0 = off) or longer, with the kind of failure, the
  path, the time taken and the start of the query text (never bind values, documents or the
  host). Connecting and sending a request now give up after `ARANGO_CONNECT_TIMEOUT` (default
  10 s, previously the 60 s request timeout) and are tried again; waiting for the answer is
  unchanged. See ops.md "Slow or failed database calls".
- The core image includes spaCy and its `en_core_web_sm` model (pinned by hash), so
  `EXTRACTION_PROVIDER=spacy` works there. The default stays `fake`, which doesn't load it. The
  `extraction-spacy` extra is now bounded to `spacy<3.9`, the model's minor version. The image's
  installed Python environment grows by about 190 MB.
- `WRITE_WORKER_CONCURRENCY` (default 1 = off): the write worker commits the agent groups of one
  claimed round in parallel, each on its own DB connection, instead of one after another. An
  agent's writes stay in order, a conflict between groups that write the same entity retries the
  batch, and a failing group lets the others finish before the round is released. Pair it with
  `WRITE_BATCH_SIZE` above 1. See ops.md "Parallel agent groups".
- Per-stage read timers on `/health`: `latency_ms` now also reports `retrieve.embed`, `.bm25`,
  `.vector_check`, `.vector`, `.facts`, `.graph`, `.fuse`, `.rerank`, `.mmr`, `.supersession`,
  `.entities`, `.neighbors`, `.assemble` and `.access`, one sample per read, so a slow read can
  be traced to its stage. No behaviour change.
- Batch commit for the write worker (`WRITE_BATCH_SIZE`, default 1 = off). The worker claims up
  to that many queued writes in one round trip, commits each tenant+agent group through the bulk
  `store_many` path, and acks them together, cutting the database round trips per write from about
  31 to about 1.3 in a local burst test. Writes with a session, working memory and full mode keep
  the per-intent path, and a failed batch falls back to per-intent retry. `WriteQueue` gains
  `claim_many` / `ack_many`. `/health` adds `write_many.*` stage timers, including the stages of the
  bulk entity pass (`write_many.entities.*`). See ops.md "Batch commit".
- Per-stage write timers on `/health`: `latency_ms` now also reports `write.embed`, `write.episode`,
  `write.memory`, `write.entities` and the stages inside entity writing (`write.entities.extract`,
  `.embed`, `.candidates`, `.match`, `.upsert`, `.edges`, `.relations`, `.relate`), one sample per
  write, so a slow write can be traced to its stage. No behaviour change.
- Entity summaries in retrieval: `RETRIEVE_ENTITY_SUMMARIES` / `opts.entity_summaries`
  (default off) appends an `Entity summaries:` section to the `/v1/retrieve` context, made
  from the Dream State summaries of the entities the hits mention. It shares
  `max_memory_tokens` with the memories (at most 20% of it).
- Neighbour expansion in retrieval: `RETRIEVE_NEIGHBOR_TURNS` / `opts.neighbor_turns` (default
  0, off) also injects the turns within that many positions of each hit, in the hit's session,
  such as the reply to a retrieved question. Neighbours only use token budget the hits left and
  appear next to their hit in turn order. Memories now store `turn_index`; the feature needs
  it together with a `session_id`, so memories written before this change have no neighbours.
  The LongMemEval harness stores each chat as its own session and gains a `+neighbors` variant.
- Fact-augmented keys (FK-1), behind `FACT_KEYS` (default off). Dream State has the LLM state
  the facts each memory carries, as short standalone sentences with relative dates resolved, and
  stores them with one embedding per fact (`FACT_KEYS_PREFIX=user:` mines only the user's turns).
  Retrieval (`RETRIEVE_FACT_KEYS` / `opts.fact_keys`, default off) then matches the query against
  the facts too, through a BM25 arm and a best-fact-cosine arm, so a turn that mentions something
  in passing is found by it; the memory itself is still what is returned. The search view gains a
  `facts` field (added to existing views at startup). The vector arm is an exact scan, skipped
  above `FACT_VECTOR_SCAN_MAX` memories per tenant. The LongMemEval harness gains a `+facts`
  variant. On the LongMemEval held-out sample it recovers a few more evidence turns but does not
  change answer accuracy, so it stays off.
- Memory-level fact supersession (GX-2), behind `FACT_SUPERSESSION` (default off). Dream State
  links a memory to the older statements it updates (`superseded_by`, `valid_to`; nothing is
  hidden). Retrieval (`opts.supersession`) ranks the current statement directly above a stale
  one it retrieved, pulling it in if missed, and marks the stale line `(superseded <time>)`.
  `/v1/dream` reports `memories_superseded`.
- `SUPERSESSION_GATE=similarity`: GX-2 candidates by embedding similarity alone (default `entity`).
- `SUPERSESSION_GATE=anchored`: GX-2 similarity candidates that also share an entity or a number
  with the new memory. This drops most same-topic, different-fact pairs before any LLM call.
  On the knowledge-update set, newest-above-stale went from 0.614 to 0.818, with 99 links and
  about 0.8 sampled precision. The similarity gate scores 0.864 but makes 155 links at about 0.3
  precision (DESIGN §23 rev 102).
- Point-in-time retrieval: `opts.as_of` (ISO date/date-time) limits retrieval to memories
  that existed by then, with supersession judged as of then.
- LongMemEval harness: `--dream` runs a Dream State pass per question; `+entities` /
  `+supersession` variant suffixes (e.g. `--rerank-scoring replace,replace+supersession`)
  compare those features on one ingest.
- LongMemEval harness: `--answer-only` reuses the ingest and Dream pass an earlier run left in
  `ARANGO_DB`, and only retrieves, answers and judges. Repeat passes measure answerer and judge
  noise for cents rather than a re-ingest. It refuses `--extract`, `--dream` and `--resume`,
  and checks every question's tenant before any LLM call.

### Changed

- Entity names ignore a possessive ending: "Vertex Foods'" and "Acme's" are "Vertex Foods" and
  "Acme". So a name that ends that way by nature ("Sotheby's") loses it too.
- The spaCy extractor no longer makes entities of a fixed list of business shorthand (ARR, CEO,
  CFO, COO, CRM, CTO, EBITDA, ERP, GAAP, IP, KPI, LOI, MRR, MSA, NDA, NRR, Q&A, SOW, SaaS) or of
  period labels (FY2025, Q4). The match is exact, so longer names containing one and other
  acronyms are kept. Entities already stored stay.
- One name is now one entity per tenant, whatever type the extractor gave it: names match ignoring
  case, spacing and a trailing legal suffix ("Acme", "ACME Inc."), including between new entities
  in one batch. Before, each type made its own entity. The entity keeps its first typed form. A new
  entity's key is derived from its tenant and name. Duplicates already stored are not merged. Two
  different things that share a name now share an entity.
- The spaCy extractor no longer makes entities of dates, times, money, percentages, quantities or
  numbers, and reads a spaced dash ("Acme — revenue") as a sentence break, which helps it find the
  name before the dash. Graphs and benchmark numbers produced with `spacy` before this differ.
- The batched write keeps entity embeddings in memory (`ENTITY_VECTOR_CACHE_MB`, default 64, 0 =
  off). Without the entity ANN index (as on ArangoGraph), resolving a batch downloaded every
  entity's embedding in the tenant; now only new ones come back. At 1536 dimensions with 90 ms of
  database delay: 2,000 entities went from 1.1 s and 65 MB per batch to 0.15 s and 0.2 MB. The
  core uses up to that much more memory.
- A batched write makes about half the database requests (4 instead of 8 for a batch with no
  semantic merges): episodes and memories are recorded in one statement, and the three kinds of
  graph edge in another. Both are all-or-nothing, so a failed edge write is now an error rather
  than silently skipped. On `/health`, `write_many.existing` and `write_many.entities.relate` are
  gone; `.insert` and `.entities.edges` cover them.
- `/v1/retrieve` and `/v1/prime` record memory access after sending the response, one database
  round trip less per request. An access update can be lost if the process dies in between. Library
  callers still write it inline. Requires `fastapi>=0.118.0`.
- A one-by-one write embeds the turn and its entity names in one provider call instead of two.
  Entity extraction now runs before the embedding; if it fails, the memory is still stored.
- With the vector index on, the API runs the BM25, vector and fact arms in parallel on extra
  database connections (up to `ARANGO_POOL_SIZE` more).
- Retrieval reads far less: the arms no longer return stored embeddings (6.7 MB → 0.31 MB per
  retrieve, median, at 1536 dimensions). They are fetched only for MMR when `MMR_LAMBDA` is below
  1.0.
- The vector-index state is cached for 60 s on request paths, and a rejected index build (as on
  ArangoGraph) is retried and logged once per 10 minutes instead of on every request. An index
  built by *another* process can take up to 60 s to be picked up.
- With the vector index unavailable and fact keys off, lite retrieval no longer embeds the query.
- The core now serves requests in parallel (PF-1a). Handlers that call the database or a model ran
  on the event loop, so one request at a time was served; they now run in a threadpool, each on its
  own leased database connection (`ARANGO_POOL_SIZE`, default 16; the durable queue keeps a second
  pool). `/health` and `/ready` have their own connection and no longer wait behind requests.
  Measured through a 90 ms database round trip: 8 concurrent retrieves went from 7.1 s to 1.2 s,
  and `/health` during them from 7.0 s to about 0.2 s. Inline (`sync`) writes, and the maintenance
  routes (Dream, salience, community, ontology, forget, seed, supersede), still run one at a time
  per process. Concurrent access-count updates on one memory are now ignored rather than raising a
  write conflict. `core/scripts/latency_bench.py` reproduces the measurement.
- Retrieval no longer spends time on MMR diversity it doesn't use. With the default
  `MMR_LAMBDA=1.0` the diversity weight is zero, but `_mmr` still computed every pairwise cosine in
  pure Python over full embeddings. At `1.0` it now sorts by relevance only (same selection order,
  same tie-breaking); below `1.0` it keeps a running max cosine per candidate instead of
  recomputing against every pick. On synthetic 1536-d candidates, 100 candidates and k=40:
  4.5 s to under 1 ms at `1.0`, and 0.25 s at `0.7`. Selection is unchanged (an oracle test checks
  it against the original formulation, ties and negative cosines included).
- LongMemEval harness (DESIGN §23 rev 104):
  - It answers with a LongMemEval prompt in place of the hallucination benchmark's
    "context only, else decline" prompt, which caused 25 of 32 misses.
  - It grades with the official per-type judge rules.
  - It defaults to `--k 20` and a new `--max-memory-tokens 8000`.
  - Scores are not comparable with earlier runs: on the same ingest, 0.644 became 0.844.
  - On a held-out sample of 91 questions it scores 0.875 against 0.747 for the old harness,
    and declines 12 of 13 unanswerable questions.
- `longmemeval_convert`: evidence labels are on by default (`--no-evidence` drops them).
  `--stratified` samples abstention questions as their own stratum. A new `--exclude
  <dataset>` draws a held-out sample.

- The background LLM moves to Claude Haiku 5.5: `BACKGROUND_MODEL` now defaults to
  `claude-haiku-5-5` (was `claude-haiku-4-5`). It is used for HyDE, the gate, Dream State,
  supersession, the Haiku extractor and the eval answerer/judge.
  - Haiku 5.5 thinks by default, so these short calls now send `thinking: disabled`.
    `BACKGROUND_THINKING=true` restores the model's default; use it for models that reject
    `disabled` (Opus 5.5, Sonnet 5.5).
  - The `anthropic` floor rises to `>=0.47.0`, the first SDK version with the `thinking` parameter.
  - The diligence-room demo defaults to `claude-haiku-5-5`.
  - The Graphiti benchmark runner stays on Haiku 4.5, because graphiti-core sends `temperature`,
    which Haiku 5.5 rejects.
  - Benchmark figures recorded before this change (LongMemEval 0.633, GX-2's 0.659) used Haiku
    4.5 as answerer and judge. Re-baseline before comparing.

### Fixed

- A write sent again after a dropped connection no longer counts twice. Entity, relationship
  and tool-step counts carry an id per write and ignore a repeat (documents gain an `ops` field
  with the last 16 ids, episodes written in batches an `op` field). A repeated batch insert no
  longer leaves its turns without graph links, a repeated queue claim takes the same intents,
  and a repeated enqueue no longer reports a unique constraint violation. No migration needed.
- A dropped connection can no longer skip rows from a query result. Asking a cursor for its
  next batch was sent again after a broken connection, and the database answered with the batch
  after the lost one, without an error. That request now raises `CursorBatchLost`, except on
  cursors opened with `allow_retry` (the entity fetch is), which get the same batch again.
- urllib3 no longer resends a request by itself after errors such as a TLS connection ending
  early; the driver's own, logged, resend handles them.
- `/health` and `/ready` no longer report the database unreachable because one connection to
  it had been dropped: a probe call whose connection breaks or is refused is tried once
  more. A call that times out is still not repeated.
- A database read that timed out is logged as `ReadTimeout` for `GET` calls too, not as
  `ConnectionError`.
- `/health` and `/ready` no longer stall behind a database call that gets no answer. Probes queued
  one behind another, and the default transport retried each timed-out read (with a 60 s request
  timeout, minutes in all). They now use their own connection with one 3 s attempt per call, share
  a check in progress, and answer within about 1.5 s, reporting `arango: false` (`/ready`: 503)
  if the database hasn't replied by then.

- A write conflict while writing entities no longer drops them. The batch was rerun, found its
  turns already stored and skipped the graph, so the memories were saved with no entity links and
  nothing reported it (the one-by-one retry did the same). Each graph write is now retried where it
  conflicts. Links already lost this way are not restored.

- The write worker no longer dies on a transient error. A DB error while claiming or acking a batch
  (or while dead-lettering) used to kill the background thread for good, so every queued write sat
  undrained until the process restarted, with nothing in `/health` to show it. The worker now logs the
  error with its traceback, hands the leased writes back, backs off (up to 30 s) and carries on.
  `/health` gains `write_worker: "running" | "stopped"`.

- JWT auth: a bearer token with an unknown `kid` made the core refetch the issuer's JWKS before
  the token was authenticated, so unauthenticated requests with random `kid`s could keep the IdP
  busy and tie up request threads. Refetches are now limited to one per 30 seconds and a fetch
  times out after 5. This needs PyJWT 2.14 or later, so the floor rises from `pyjwt>=2.9.0` to
  `pyjwt>=2.14.0`.
- GX-2 supersession verdicts: the LLM now explains in one or two sentences, then ends with an
  `ANSWER: <numbers|NONE>` line (256-token cap, was 32). Only that line is parsed, strictly. The
  bare-number format made Haiku 5.5 reply in prose that the cap cut off before the number, and
  the old parser accepted any digit in a reply, so prose like "statement 1 says…" could create a
  link. Replies without a well-formed answer line link nothing and are counted as `unparsed`.
- GX-2 supersession verdicts: the cap rises from 256 to 640 tokens. The explanation covers each
  older statement, and with 4–5 candidates about one reply in ten was cut off before its answer
  line. The prompt now also requires the *same fact about the same thing*: a shared topic, a
  different item or occasion, or a statement with no fact (a request or small talk) is not an
  update, while a clearly implied new value ("added another one") is. In a labelled replay,
  precision went from 0.79 to 0.88 and recall from 0.79 to 0.82, but on a fresh KU run the
  ordering metric and link counts did not change (DESIGN §23 rev 101).
- The `[crewai]` extra is now marked `python_version < '3.14'`, matching crewai's own cap. On
  Python 3.14, pip skipped every current crewai and fell back to an old, uncapped release that
  crashes on import (chromadb's pydantic-v1 settings). The CrewAI CI job is pinned to Python 3.12,
  since `ubuntu-latest`'s default `python3` moved to 3.14.
- The `[mcp]` extra now requires `mcp>=1.21.1,<2` (was `>=1.14.0`). A fresh install resolved
  mcp 2.x, which renamed FastMCP (`mcp.server.fastmcp` no longer exists), so the MCP server
  failed on import. mcp releases before 1.21.1 also fail to import with pydantic 2.14. `make
  mcp-floor` now smoke-tests both the lowest and the newest allowed release, resolved fresh as a
  user's install would be.
- Dream State distillation reads at most `DREAM_DISTILL_MAX_MENTIONS` (20) of an entity's
  most recent memories. Before, it sent every mentioning memory in one LLM prompt.

## [0.1.0] — 2026-09-30

First public release. Everything below is the initial public surface, published to
PyPI (`arango-memory`), npm (`@arango-memory/vercel`), and GHCR (the core image)
from the `v0.1.0` tag.

### Added — core

- Ingestion write path: episodes (WORM) + memories + entity/relation extraction,
  idempotency-keyed so retries and replays never duplicate.
- Hybrid retrieval: BM25 + Faiss vector + graph traversal → RRF fusion → MMR
  diversity → tiered token-budget assembly. Lite mode (zero hot-path LLM calls)
  and full mode (adaptive gate + HyDE).
- Multi-hop retrieval (RQ-1): `mode="multihop"` decomposes a query into independent
  sub-lookups (one LLM call), retrieves each, and RRF-fuses the results a second time
  so evidence corroborated across sub-questions ranks up — the path to multi-hop-category
  recall. A ≤1-lookup decomposition falls back to the exact single-shot path, so the mode
  cannot regress single-hop. Knobs: `DECOMPOSE_MAX_SUBQUERIES`, `DECOMPOSE_MAX_HOPS`.
  (Benchmark-dependent: neutral/harmful on LoCoMo's single-turn evidence, but +0.165
  all-hops recall on MuSiQue's genuine multi-evidence; see DESIGN §23.)
- Benchmark expansion (BX-1): a **multi-evidence recall metric** (`recall-frac` = fraction
  of a question's support set retrieved, reported alongside all-hops `Recall@k`) and a
  **MuSiQue-Ans converter** (`eval.musique_convert`), so multi-hop *retrieval* can be
  measured on a genuinely multi-evidence benchmark. Backward-compatible: LoCoMo single-fact
  runs are unchanged.
- Cross-encoder reranker (RQ-2b): an opt-in `rerank=true` flag inserts a pluggable
  `Reranker` (keyless `FakeReranker`; a local sentence-transformers cross-encoder via the
  `rerank` extra) between fusion and MMR, re-scoring the top `RERANK_TOP_N` candidates by
  joint (query, passage) relevance — the diagnosed fix for in-pool-but-unranked evidence
  (RQ-2a). Composable with lite/multihop; off the hot path; degrades to the fused order on
  failure. A retrieval-miss diagnostic (`eval.pool_diag`) reports the ranking-vs-recall
  split that motivates it.
- Rerank scoring modes (RQ-3): `RERANK_SCORING` picks how the reranked block is scored —
  `replace` (default, unchanged: cross-encoder only), `rrf` (rank-blend of cross-encoder and
  fused rank, restoring arm consensus/recency), or `event_time` (the cross-encoder
  probability + `RERANK_TIME_WEIGHT` × content-time newness, so an updated statement of a fact
  outranks the stale one). **Measured (DESIGN §23): keep `replace`** — `event_time` fixes update
  ordering but hurts temporal-reasoning accuracy (0.654 → 0.368) and recall; `rrf` is neutral. `retrieve()` takes per-call `rerank_scoring` / `rerank_time_weight`
  overrides and a `record_access=False` read-only probe (skips the spaced-repetition refresh).
- Per-request candidate pool (RT-1): `CANDIDATE_POOL` (default 100) is the per-arm candidate
  count before fusion/rerank/MMR, also overridable per call via `opts.candidate_pool`. Raise it
  (e.g. 500) **with rerank** on an open/large corpus to recover tail-reachable evidence, at more
  per-query DB work (BX-3, DESIGN §23).
- Single-large-tenant scalability (SC-1) — keeps store/retrieve latency flat as one tenant grows
  to thousands of memories, the wall the pooled-corpus run hit:
  - **ANN entity resolution** (SC-1b): write-time entity dedup matches a new entity against the
    **top-k nearest** via a Faiss index on `entities` instead of full-scanning the tenant — fixes
    O(N²) ingestion. Falls back to the scan below the index's training threshold. Knobs:
    `ENTITY_VECTOR_N_LISTS`, `ENTITY_VECTOR_TRAIN_FACTOR`, `ENTITY_RESOLUTION_TOP_K`.
  - **Bounded graph fan-out** (SC-1c/SC-1d): the graph arm caps both the `relates_to` neighbours
    (`GRAPH_MAX_NEIGHBORS`, 200) and the memories expanded per entity
    (`GRAPH_MAX_MEMORIES_PER_ENTITY`, 50), so a dense tenant can't explode retrieval. Defaults
    leave real corpora untouched. Profiler proof (`eval.scaling_profile`, SC-1a): store p50 flat
    ~270 ms and retrieve p50 flat ~685 ms across 500 → 3,000 memories (DESIGN §23).
- Bitemporal validity, PII redaction at ingest, right-to-be-forgotten.
- In-process lifecycle passes (keyless, no Pregel): PageRank salience, label-
  propagation community detection, Dream-State consolidation, Ebbinghaus lazy
  decay, ontology-evolution proposals.
- Working-memory tier (TTL + capacity cap) and GAM session topic-shift trigger.
- HTTP API over `/v1`; entity + graph read APIs; `stats`/`health`.
- Read-your-writes for handoffs (MA-1): `store`/`step` accept `sync: true` (commit
  inline + force search-view visibility before responding), and `POST /v1/flush`
  is a per-tenant barrier that blocks until the queue drains and the view is synced.
- Multi-agent reads (MA-2): `retrieve` accepts `ctx.read_agent_ids` to fuse across
  several agents in one pass (all three arms filter `agent_id IN`); each hit carries
  its writer's `agent_id` as provenance. CrewAI tiers read across all three namespaces
  in one call. Writes and tenant isolation are unchanged.
- Task briefing (MA-3): `POST /v1/prime` composes one budgeted handoff briefing —
  retrieved history + key entities (from the hits' mentions) + prior tool runs across
  `read_agent_ids` — so the next agent starts warm. Read-only; spans MA-2 agents.
- Adapter surface for handoff: the Vercel middleware gains `captureResponses` (stores the
  model's reply, MA-4), `readAgentIds` (MA-2), and `syncWrites` (MA-1), plus standalone
  `prime()`/`flush()` helpers; the MCP server gains `prime` + `flush` tools and
  `read_agent_ids` on `search`.
- Handoff eval (MA-5, `eval/handoff.py` + `make handoff-eval`): scores the multi-agent
  path — a writer ingests facts + tool runs, a reader `prime`s across `read_agent_ids`
  after a barrier — on context + procedural recall. Keyless smoke slice gates CI.
- Orchestration guide (MA-6, `docs/orchestration.md`): the end-to-end handoff pattern —
  naming conventions, a worked planner→researcher→writer pipeline, per-harness recipes,
  and the orchestrator/brain seam.

### Added — durability & operations

- Durable write path: pluggable `WriteQueue` (in-memory default, ArangoDB-backed
  for production) with claim/ack leasing, dead-letter (`failed_writes`), and
  `ops replay`. Multi-instance via a shared `arango` queue.
- `ops` CLI: `vector-rebuild`, `embeddings-migrate`, `replay`, `explain`, and `mem-sample`
  (arangod memory metrics as JSON lines: RSS, block cache, write buffers, the untracked
  remainder, and the entities/memories held across all databases — run it alongside a long
  benchmark to see what grows).
- Persistent scope indexes backing every hot-path tenant/agent/invalid_at filter.

### Added — security

- Bearer API-key auth (`API_KEYS`), open-by-default; tenant/scope derived from the
  key. Per-tenant rate limiting + request-size caps.
- **OIDC / JWT auth** (`OIDC_ISSUER`) — verify signed bearer tokens from an external
  IdP against its JWKS (RS256 allowlist, `exp`/`nbf`/`iss`/`aud`, claims → tenant/scope);
  coexists with static keys, fail-closed, short-TTL revocation.

### Added — observability

- OpenTelemetry spans + meters (`memory.*`), an in-process metrics emitter,
  structured JSON/text logging with `X-Request-ID` correlation, and in-process
  p50/p95/p99 latency percentiles on `/health`.
- Sample OTEL collector + Prometheus + Grafana dashboard (`deploy/observability/`).
- **Liveness/readiness split** — `/health` (always `200`, process liveness) + `/ready`
  (`200`/`503` on DB reachability) for orchestrator probes.

### Added — adapters & apps

- `@arango-memory/vercel` AI SDK middleware client, MCP server, LangChain /
  LangGraph / CrewAI integrations, and the Memory Dungeon reference app.
- **MCP server over Streamable HTTP.** `python -m arango_memory.mcp --transport http` runs the MCP
  server as a standalone network service at `/mcp` (stdio stays the default). Auth is pass-through:
  each request must carry the caller's own bearer credential (401 otherwise), forwarded to the core
  so its ABAC applies per caller; the server's own `ARANGO_MEMORY_API_KEY` is never lent to HTTP
  callers. It binds localhost by default with DNS-rebinding protection, refuses non-local binds
  without `--allowed-hosts`, and is stateless for load balancers. Tool calls now run off the event
  loop, so concurrent clients don't wait on each other. The `mcp` extra's floor rises to `>=1.14.0`,
  the first release this works on, checked by `make mcp-floor` in CI.
- **One image, core or MCP (MCP-2).** The container image now includes the `mcp` extra (+1.5 MB), so
  `python -m arango_memory.mcp --transport http` runs from the same image as the core. The image puts
  its venv first on `PATH`, the Docker healthcheck URL is configurable (`HEALTHCHECK_URL`), the MCP HTTP
  server answers an unauthenticated `GET /health`, and its port falls back to the platform `PORT`. There
  is a `docker compose --profile mcp` service, a TLS reverse-proxy and Railway recipe
  (`docs/adapters/mcp.md`), and a CI smoke test of the image's MCP role.
- **CrewAI ≥ 1.10 unified-memory backend (CW-1).** `arango_crewai_memory(db, tenant_id=…, agent_id=…)`
  returns a crewai `Memory` backed by the core, for `Crew(memory=…)`. crewai's `StorageBackend.search()`
  receives only a query embedding, so the backend ships with a paired embedder (the core's embedder, which
  remembers the text behind each vector): searches recover the query text and run the core's full hybrid
  retrieval (BM25 + vector + graph + rerank), falling back to exact cosine search for a vector it didn't
  produce. crewai's per-record fields (scope, categories, metadata, importance, timestamps, privacy) live in
  a new `crewai_records` collection joined to the core memory, so the text stays single-sourced and
  PII-redacted, and tenant `forget` / `purge` cover it. Scores are cosine similarity, as crewai's Qdrant
  backend reports. The legacy `to_crewai_storage()` shim stays for `crewai<1.10`.
- Core: `forget_memories(db, tenant_id=…, memory_keys=…)` soft-deletes specific memories by key.
- CI: a `crewai` job runs the adapter tests and mypy against a real crewai install (`make test-crewai`),
  so an upstream interface change fails CI instead of slipping past a stub.

### Added — quality

- Embedding cache (per-tenant), batch entity embedding, hallucination / noise-
  reduction eval, LoCoMo-style smoke benchmark + a **real-data LoCoMo converter**
  (`eval/locomo_convert.py`) for the BYO benchmark run.
- Hardening: concurrency / multi-tenant isolation tests, failure-injection +
  graceful-degradation tests, authz-breadth tests, and a deterministic
  perf-regression gate.
- **LongMemEval end-to-end validation** (`eval/longmemeval.py`, stratified-90, real
  `openai` embeddings + `anthropic` answerer/judge): the graph earns its keep — substrate
  (BM25+vector) **0.411** → product (graph ON + reranker) **0.522**, of which the entity
  graph is +0.089 and the reranker +0.022. Extractor rung (HX-1d): the **haiku LLM
  extractor** lifts it further to **0.589** (+0.067 over spaCy), so `haiku` is the
  accuracy-max extractor while spaCy stays the keyless default. See DESIGN §23.
- **Paired rerank-scoring evaluation** (RQ-3) in the LongMemEval harness:
  `--rerank-scoring replace,rrf,event_time:0.2` scores every variant against one ingest with an
  exact McNemar test vs the first; `longmemeval_convert --evidence` carries the `has_answer`
  turns + session dates, adding deterministic **evidence recall@k** and knowledge-update
  **newest-above-stale** metrics; `--retrieval-only` reports just those (no LLM spend);
  `--types` / `--offset` carve disjoint dev/test splits; `--checkpoint` appends each question's
  scores as it finishes and `--resume` continues a crashed run (purging the interrupted tenant). A failed answer call no longer kills a long run — it's
  counted, scored incorrect, and dropped from the paired accuracy test.

### Added — scaling (optional)

- **Optional Redis shared layer** (`REDIS_URL`, the `redis` extra) — a cross-instance
  rate-limit budget (vs per-instance N×) and a shared embedding cache; both fail-soft
  (limiter fails open, cache falls through). The write queue stays ArangoDB-backed.

### Added — packaging & release

- Single version source of truth (`pyproject.toml` → `__version__` → OpenAPI).
- FastAPI **OpenAPI docs** surfaced + grouped by tag (`/docs`, `/redoc`, `/openapi.json`),
  public even under auth.
- **Gated release pipeline** (`.github/workflows/release.yml`): a `v*` tag builds the
  core wheel/sdist, the `@arango-memory/vercel` tarball, and the container image with a
  CycloneDX SBOM + dependency scan each; publishing no-ops until registry credentials
  are added. Full package metadata; **Apache-2.0** `LICENSE` + `NOTICE`.
- **Hardened container** — digest-pinned base, non-root user, `HEALTHCHECK`; a CI image
  build-smoke runs on every PR.

### Changed — dependencies

- **Vercel AI SDK (`ai`) → `5.0.237`** across the `@arango-memory/vercel` adapter and all example
  apps (was `5.0.220`). Latest v5; no breaking changes to `LanguageModelV2Middleware` /
  `@ai-sdk/provider@2`. The adapter's `peerDependency` stays `^5.0.0` (broad consumer compat).
- **`workflow` → `5.0.0-beta.42`** in the Diligence Room example (was `beta.37`) — newest v5 beta;
  `next build` + typecheck + tests verified.

### Changed — examples

- **Diligence Room — "why shared memory" spotlight.** The memo's closing takeaway (the point of the
  demo) is rebuilt from a flat list into a card grid that separates each capability's **value**
  (why, always shown) from its **mechanism** (how — `read_agent_ids`/`/v1/flush`,
  `valid_time`/`Supersedes`, corroboration-weighted belief, source provenance — behind a "How it
  works" disclosure), plus a compact "Why this worked" teaser pinned to the War Room. The verdict,
  Risks, and Strengths are unchanged.
- Diligence Room — per-document claim extraction within a specialist now runs concurrently
  (bounded, `DILIGENCE_EXTRACT_CONCURRENCY`), cutting a live run's slow phase.
- Memory Dungeon — the `talk` tool's memory writes are parallelized and batched: an NPC's
  testimony is enqueued concurrently and all claim subjects are minted in one `/v1/seed` call
  (was a serial store→seed round trip per claim), so the turn no longer blocks on ~2N core hops.
- Reference agent (`examples/vercel-agent`) — reviewed for the same batching/parallelism and
  intentionally left unchanged: a minimal single-turn loop with no per-item fan-out, and its two
  demo turns are sequential by design (turn 2 recalls turn 1's memory).
- MCP memory (`examples/mcp-memory`) — reviewed and intentionally left sequential: parallelizing its
  per-tool calls would misrepresent how MCP hosts drive tools (one at a time per turn) and gain
  little (stores are queued; the flush dominates), and the linear flow is clearer for a teaching demo.

### Fixed — core

- **Batched entity resolution degraded with other tenants' entities and OOM-killed ArangoDB.**
  An RQ-3 LongMemEval run (one tenant per question, ~1.5k entities each) slowed to repeated
  slow-query warnings and was OOM-killed at ~155k shared `entities`. `EXPLAIN` on 3.12.9 showed the
  vector index *is* used with the tenant filter pushed in, but filtered ANN widens its search until
  it finds top-k rows of the tenant, fetching each candidate (a 1536-dim document) to test
  `tenant_id` — so its cost grew linearly with *other* tenants (200 query vectors: 0.17s at 10k
  others → 1.7s at 100k), with `nProbe` never passed. The batch query also had a correctness bug:
  its SORT/LIMIT sat at the top level of the nested `FOR`, so it returned top-k rows for the whole
  batch rather than per entity, and most entities resolved against the wrong candidates (missed
  merges). Tenants with ≤ `ENTITY_RESOLUTION_SCAN_MAX` (5000) entities now resolve by an exact
  tenant-scoped scan, whose cost doesn't depend on other tenants. Larger tenants use a per-vector
  subquery that returns keys only (`{nProbe}` passed, 64 vectors per round trip), then one fetch of
  the distinct rows.
- **Extraction concurrency slowed CPU-bound extractors ~3x.** The IN-7 thread pool
  (`EXTRACTION_CONCURRENCY`, default 8) wrapped every extractor, but spaCy (and the regex/GLiNER
  tiers) mostly hold the GIL, so threads just convoyed: a 413-turn LongMemEval question with
  `EXTRACTION_PROVIDER=spacy` took 2m14s at 8 threads (≈356s system CPU) vs 42s at 1, identical
  output. Extractors now declare `io_bound`; the pool is used only for I/O-bound (LLM) extraction
  — `haiku`, or `layered` with a Haiku tier — and the rest run sequentially. Custom extractors
  without the attribute keep the threaded path; set `io_bound = False` to opt out.
- **Vector retrieval under-probed the trained index.** `settings.n_probe` was defined but never
  passed to `APPROX_NEAR_COSINE`, so once a tenant's Faiss IVF index trained it searched ArangoDB's
  default of a single cell — losing ~30% recall versus the exact pre-training scan (surfaced by the
  HX-2 recall-vs-corpus sweep: the vector arm collapsed to 0.40 recall-frac at 3,075 docs). The
  query now passes `{nProbe: settings.n_probe}`; recovery is ~0.41 → ~0.61 recall-frac at the
  default (`n_probe=10`), and nProbe is nearly free (retrieval latency is embedding-round-trip-bound).
- **`store_many(extract=True)` double-counted the graph on replay.** The batched graph pass fed
  every item to `write_entities_many` whether or not its episode already existed, so re-running a
  batch (e.g. a benchmark against an existing DB with `--extract`, or a library retry) added again
  to entity `mention_count`/`belief` and `relates_to` `corroboration`. It now checks which episode
  keys already exist in one AQL round trip and graph-reflects only new ones, matching `store()`'s
  `is_new` gate (§8); replayed items return empty `entity_ids`.
- **Rerank silently truncated results when `k > RERANK_TOP_N`.** `_rerank` returned only the
  reranked head, dropping every fused candidate past `rerank_top_n` — contrary to the documented
  "the rest keep their fused order below the reranked block". The tail is now kept below the head
  in its fused order, re-scored just under the head's minimum (RRF scores and cross-encoder logits
  aren't on a common scale, so appending raw scores could rank a tail item above the head). No
  change at the defaults (`k=10`, `rerank_top_n=50`, `MMR_LAMBDA=1.0`); with `MMR_LAMBDA < 1`,
  MMR's diversity term can now also draw from the tail.

- **Local ArangoDB sized its caches for the whole Docker VM.** In `docker-compose.yml` arangod
  auto-sized a 1.85 GB block cache and ~6.7 GB of AQL memory from the 8 GB VM it shares with the
  core, and after the RQ-3 LongMemEval rerun an idle, empty server still held 4.19 GB RSS (6.08 GB
  peak). Compose now sets `ARANGODB_OVERRIDE_DETECTED_TOTAL_MEMORY` from `ARANGO_DETECTED_MEMORY`
  (default `4G` → 512 MiB block cache, 3.24 GB AQL). The entities Faiss index was ruled out:
  measured at 100k 1536-dim entities, an index no query touches costs nothing measurable at rest.

### Fixed — retrieval

- **`event_time` rerank scoring squashed relevance with a second sigmoid.** It computed
  `sigmoid(ce) + β × newness`, assuming raw cross-encoder logits, but sentence-transformers already
  returns sigmoid probabilities — so relevance was compressed into ~[0.5, 0.73] and `RERANK_TIME_WEIGHT`
  acted ~4–5× stronger than set. β now adds on the probability scale, and the `Reranker` protocol
  documents a [0, 1] score contract. Opt-in mode only (`replace`, the default, and `rrf` are
  unaffected); the RQ-3 write-up (DESIGN §23) is corrected and its decision stands.
- **The local reranker crashed the process under concurrency on Apple Silicon.** `get_reranker()`'s
  `lru_cache` let concurrent first calls each build their own cross-encoder, so N threads loaded N
  copies of the model onto MPS at once. Concurrent `predict()` on one model is also unsafe on MPS. Either
  one killed the process with no Python traceback: a Metal command-buffer assertion or a segfault. Found
  running the LongMemEval harness with `--concurrency 4 --rerank`; the API server's concurrent requests
  were exposed too. The first build now happens under a lock, and each instance serializes `predict()`.

### Fixed — adapters

- **CrewAI `to_crewai_storage()` on crewai ≥ 1.10 now fails with a clear message.** CrewAI 1.10.0
  removed the legacy `crewai.memory.storage.interface.Storage` (and `ExternalMemory`) the shim
  subclasses, so on current crewai it died with an opaque `ModuleNotFoundError` — CI missed it
  because the shim test stubs crewai. It now raises an `ImportError` naming the cause and pointing to
  `ArangoCrewStorage` / `crew_memory()`, which don't import crewai and work on any version. The shim
  supports `crewai<1.10`; crewai ≥ 1.10 uses the new unified-memory backend (`arango_crewai_memory()`, above).

### Fixed — examples

- **Live demos patched for a critical Next.js advisory** (unauthenticated RCE in the Image
  Optimization API, plus SSRF / DoS / cache-confusion advisories): Memory Dungeon and Diligence Room
  move Next.js 15.5.19 / 15.5.21 → **15.5.26** (same major). Diligence Room also overrides
  `@workflow/core`'s exact `nanoid@5.1.6` pin to the patched **5.1.16** — upstream's latest beta still
  pins the vulnerable version, and npm's suggested "fix" was a breaking downgrade of `workflow` to
  2.0.6. Remaining advisories need major upgrades: postcss bundled in Next (fixed only in Next 16) and
  undici under the AI SDK (fixed only in `ai` v7).
- **Demos re-patched after a pre-release audit.**
  - **Next.js 15.5.26 → 15.5.27** in both demos. Next pins an exact, vulnerable **postcss 8.4.31** for its
    build-time CSS processing; an `overrides` entry moves it to 8.5.28 (semver-minor), which clears the
    **postcss highs** without moving to Next 16. Production builds and CSS output were verified.
  - **`@ai-sdk/gateway` ^1 → ^2** in both demos, aligning it with the `ai@5` line it already runs on
    (same provider spec). This drops a vulnerable `@ai-sdk/provider-utils` 3.0.12.
  - **The Diligence Room moves from `workflow` 5.0.0-beta.56 to the 5.0.0 stable release**: 23 steps still
    compile, and the `nanoid` override is still needed.
  - **Result:** production findings drop to 0 critical / 1 high in each demo (Dungeon 2H/8M → 1H/7M,
    Diligence 2H/19M → 1H/3M). Everything left is AI SDK v5's `undici` 5 dependency, which is fixed
    only by the AI SDK v7 migration.
- MCP memory (`examples/mcp-memory`) — the demo now recalls reliably on a **cold core** (fresh
  tenant, `vector: deferred`, BM25-only retrieval). Each recall query carries a light lexical anchor
  that also appears in its target memory (`allergic`, `Mira`, `Munich`), so BM25 alone surfaces the
  right memory even before the vector index trains; real embeddings add semantic matching on top.
  Verified 3/3 against a genuinely cold DB (was 2/3, missing the one pure-semantic query).
- Diligence Room (`examples/diligence-room`) — fixed a red-team phase failure and made failures
  diagnosable. The dispute finder is one large structured-output (`generateObject`) call; without an
  output cap its JSON could **truncate mid-response** and throw, failing the phase (which also
  skipped synthesis and lost the memo). Cap `maxOutputTokens`, and on any failure log the real cause
  and degrade to zero disputes so the run still reaches synthesis. The campaign's `phase()` wrapper
  now also `console.error`s caught errors, so a failed phase shows its cause in the platform logs
  instead of being a black box. Confirmed fixed against the live deployment.

### Security

- **Core runtime dependencies patched** (these ship inside the container image, which installs
  from `uv.lock`): starlette 1.2.1 → 1.7.0 (with FastAPI 0.136 → 0.141), cryptography 48.0.0 →
  50.0.1, anyio 4.13.0 → 4.15.1 — clearing all 13 known advisories in the runtime set (`pip-audit`).
- **PyJWT 2.13.0 → 2.15.1 and urllib3 2.7.0 → 2.8.0** (runtime, so in the image): 12 PyJWT
  advisories, relevant because PyJWT verifies JWT credentials on the core's auth path, plus 3 in
  urllib3. All were published after the first audit. oauthlib 3.3.1 → 4.0.0 in the `crewai` extra's
  chain. The runtime set audits clean again.
- **Image OS layer hardened.** The image build applies Debian security updates newer than the
  pinned base: OpenSSL `3.5.7-1~deb13u2 → deb13u3` (13 advisories each in `openssl` / `libssl3t64` /
  the legacy provider, some High) and libpcre2 (High). It also removes the base image's unused system
  pip (the app runs from its uv venv).
- **Image moved to Python 3.14** (`python:3.14.7-slim`, index-digest pinned). Every CPython line still
  carries some interpreter advisories, but 3.11–3.13 each have a fixable **High** one; 3.14 has only
  Medium/Low. Fixable image findings drop 59 → **4**, all Medium/Low. The published wheel still supports
  Python ≥ 3.11, and CI now runs the core suite on both 3.11 (the floor) and 3.14 (the image).
- **Release gate:** pip-audit only sees Python dependencies, so the release workflow now also runs a
  **Grype scan of the built image** and fails on fixable High/Critical findings before anything is
  pushed. Any exception must be listed with its reason in `.grype.yaml`, which currently has none.
- **Optional extras patched:** crewai 1.14 → 1.15 (clears its pinned mcp / json-repair advisories),
  gliner 0.2.29 + transformers 5.15, torch 2.14, pillow 12.3, aiohttp 3.14.3, datasets, setuptools.
  One residual: **chromadb 1.1.1** (via the `crewai` extra) has advisories with no fixed release and
  is pinned `~=1.1.0` by crewai itself.
- **Release SBOM for the Python package now lists its dependencies.** The release workflow pointed
  syft at the built wheel, which it can't catalog (0 components); it now catalogs the pinned
  runtime export (`uv export --no-dev`, no extras).
- **`core/.dockerignore`** keeps `.env`, benchmark data, and caches out of the Docker build context, so
  a future broad `COPY` can't bake secrets into a published image.

[0.1.0]: https://github.com/mikefons/arango-agentic-memory/releases/tag/v0.1.0
