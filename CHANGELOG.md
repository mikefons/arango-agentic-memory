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
- Bitemporal validity, PII redaction at ingest, right-to-be-forgotten.
- In-process lifecycle passes (keyless, no Pregel): PageRank salience, label-
  propagation community detection, Dream-State consolidation, Ebbinghaus lazy
  decay, ontology-evolution proposals.
- Working-memory tier (TTL + capacity cap) and GAM session topic-shift trigger.
- HTTP API over `/v1`; entity + graph read APIs; `stats`/`health`.

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

[Unreleased]: https://github.com/mikefons/arango-agentic-memory/commits/main
