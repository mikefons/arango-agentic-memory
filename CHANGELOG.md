# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once `1.0.0`
ships. Until then (`0.x`), minor versions may carry breaking changes; see
[Versioning](docs/DESIGN.md#25-development-tooling--infrastructure).

The Python core (`arango-memory`) and the Vercel adapter (`@arango-memory/vercel`)
are versioned together and released from this repository.

## [Unreleased]

First planned release (`0.1.0`). Everything below is the initial public surface;
it has not yet been tagged or published to a registry.

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
- `ops` CLI: `vector-rebuild`, `embeddings-migrate`, `replay`, `explain`.
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

### Added — quality

- Embedding cache (per-tenant), batch entity embedding, hallucination / noise-
  reduction eval, LoCoMo-style smoke benchmark + a **real-data LoCoMo converter**
  (`eval/locomo_convert.py`) for the BYO benchmark run.
- Hardening: concurrency / multi-tenant isolation tests, failure-injection +
  graceful-degradation tests, authz-breadth tests, and a deterministic
  perf-regression gate.

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
  are added. Full package metadata; **MIT** `LICENSE`.
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

### Fixed — examples

- MCP memory (`examples/mcp-memory`) — the demo now recalls reliably on a **cold core** (fresh
  tenant, `vector: deferred`, BM25-only retrieval). Each recall query carries a light lexical anchor
  that also appears in its target memory (`allergic`, `Mira`, `Munich`), so BM25 alone surfaces the
  right memory even before the vector index trains; real embeddings add semantic matching on top.
  Verified 3/3 against a genuinely cold DB (was 2/3, missing the one pure-semantic query).

[Unreleased]: https://github.com/mikefons/arango-agentic-memory/commits/main
