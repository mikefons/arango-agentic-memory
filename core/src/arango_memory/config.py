"""Environment-driven settings for the memory core."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Access scopes, ordered least → most privileged (MA-7). `consolidate` is a superset
# of `write` (may also do normal writes) and is required to write an `*::insight` tier.
Scope = Literal["read", "write", "consolidate"]
_SCOPE_RANK: dict[str, int] = {"read": 0, "write": 1, "consolidate": 2}


def scope_allows(have: str, need: str) -> bool:
    """True if `have` is at least as privileged as `need` (MA-7 ordered scopes)."""
    return _SCOPE_RANK.get(have, -1) >= _SCOPE_RANK.get(need, 99)


class ApiKeyEntry(BaseModel):
    """What an API key grants (DESIGN.md §17): a tenant, a scope, and optionally the
    set of agents it may act as (MA-7). `agent_ids=None` → any agent (default). Entries
    support a glob-lite suffix (`"research::*"`) to grant a whole crew tier."""

    tenant_id: str
    scope: Scope = "read"
    agent_ids: list[str] | None = None


class Settings(BaseSettings):
    """Core configuration, populated from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ArangoDB connection target. "local" = Docker container; "arangograph" =
    # Arango's managed cloud. Informational (drives defaults/telemetry); the
    # actual connection is driven by the url/auth/tls fields below.
    arango_target: Literal["local", "arangograph"] = "local"

    # ArangoDB
    arango_url: str = "http://localhost:8529"
    arango_db: str = "agentic_memory"
    arango_username: str = "root"
    arango_password: str = "changeme"
    arango_bearer_token: str | None = None
    # TLS verification for HTTPS endpoints (ArangoGraph). True verifies certs;
    # may be a CA-bundle path. Ignored for plain-http local connections.
    arango_tls_verify: bool = True
    arango_request_timeout: int = 60

    # Embeddings
    openai_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    # "openai" (real) or "fake" (deterministic, no key — tests/sim). Default
    # "fake" so dev/test/CI run keyless; the reference app/prod set "openai".
    embedding_provider: Literal["openai", "fake"] = "fake"
    # Dimensionality for the fake embedder + the vector index when fake is used.
    embedding_dimensions: int = 256

    # Background / extraction LLM
    anthropic_api_key: str | None = None
    background_model: str = "claude-haiku-4-5"
    # "anthropic" (real) or "fake" (deterministic, no key — tests/sim). Used by
    # full-mode enrichment (HyDE, adaptive gate). Default "fake" so dev/CI run keyless.
    generation_provider: Literal["anthropic", "fake"] = "fake"

    # Reranker (RQ-2b): a cross-encoder re-scores the fused candidate pool by joint
    # (query, passage) relevance before MMR — the diagnosed fix for in-pool-but-unranked
    # golds (§9, §23). Opt-in and off the lite hot path; degrades to the fused order if the
    # model is unavailable. "local" needs the `rerank` extra (sentence-transformers).
    rerank_enabled: bool = False
    reranker_provider: Literal["local", "fake"] = "fake"
    reranker_model: str = "BAAI/bge-reranker-base"
    # How many top fused candidates to re-score (cost scales with this); the rest keep their
    # fused order below the reranked block.
    rerank_top_n: int = Field(default=50, ge=1)

    # Core service
    core_host: str = "0.0.0.0"
    core_port: int = 8080

    # Durable write path (DESIGN.md §15)
    write_max_retries: int = Field(default=5, ge=1)
    write_backoff_base: float = Field(default=0.5, ge=0.0)

    # Entity extraction (DESIGN.md §8 Stage 2). Tiers behind the `Extractor`
    # Protocol: "fake" (deterministic, no models — tests/sim), "spacy" (NER, the
    # `extraction` extra), "gliner" (GLiNER NER + GLiREL typed relations, torch),
    # "haiku" (LLM via the generator), "layered" (spaCy→GLiNER→Haiku chain).
    extraction_provider: Literal["fake", "spacy", "gliner", "haiku", "layered"] = "fake"
    spacy_model: str = "en_core_web_sm"
    gliner_model: str = "urchade/gliner_mediumv2.1"
    # Candidate entity labels GLiNER scores against, and the relation labels
    # GLiREL/Haiku may emit (coerced into the §5 enum at write time).
    gliner_entity_labels: tuple[str, ...] = (
        "Person", "Organization", "Location", "Event", "Object", "Concept",
    )
    relation_labels: tuple[str, ...] = (
        "caused_by", "occurred_during", "subtopic_of", "associated_with",
    )
    # LayeredExtractor escalates to the Haiku tier only when the cheaper tiers
    # (spaCy + GLiNER) yield fewer than this many entities (0 disables escalation).
    extraction_escalate_below: int = Field(default=1, ge=0)
    # Write-time conflict thresholds (DESIGN.md §8 Stage 3): cosine vs existing
    # entities — ≥ merge → same entity; ≥ flag → create + mark for Dream State.
    entity_merge_threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    entity_flag_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    # Corroboration → belief (DESIGN.md §8/§12): belief = confidence_prior ×
    # (1 − (1 − base)^reliability_sum). Each corroborating episode adds its
    # source_reliability to reliability_sum; `base` is the per-evidence increment.
    corroboration_base: float = Field(default=0.5, gt=0.0, lt=1.0)
    # Consolidation / Dream State (DESIGN.md §13).
    consolidation_mention_threshold: int = Field(default=5, ge=1)
    dream_breaker_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    # Ontology evolution (DESIGN.md §13, v2 research): propose typed relationships
    # from recurring associated_with clusters, human-in-loop. Off by default.
    ontology_evolution: bool = False
    # Min distinct associated_with edges per label-pair to propose a type.
    ontology_min_support: int = Field(default=3, ge=1)

    # Security (DESIGN.md §17): redact PII at ingestion before anything is persisted.
    redact_pii: bool = True

    # Enrichment mode (DESIGN.md §10)
    memory_mode: Literal["lite", "full"] = "lite"
    # Full-mode adaptive gate (§9 stage 1): spend an LLM call to decide whether the turn
    # needs memory at all, skipping retrieval when not. Pure cost optimization — a wrong
    # SKIP returns an empty result, i.e. an unrecoverable miss. Set false when every turn
    # needs memory (QA/eval workloads); then full mode is HyDE-only and makes no gate call.
    adaptive_gate: bool = True

    # Retrieval defaults (DESIGN.md §9)
    max_memory_tokens: int = Field(default=1500, ge=0)
    n_probe: int = Field(default=10, ge=1)
    k: int = Field(default=10, ge=1)
    # How many candidates each arm pools before fusion/rerank/MMR. On an open/large-tenant
    # corpus, widening this (+ rerank) recovers the tail-reachable golds BX-3 found (§23) —
    # at more per-query DB work. Default 100 keeps the common (given-context) path cheap.
    candidate_pool: int = Field(default=100, ge=1)
    # MMR relevance↔diversity balance for the final top-k re-rank (§9). 1.0 = pure
    # relevance (fusion order); lower trades relevance for diversity in the returned set.
    # Defaults to pure relevance: diversity is a *context-window* concern (don't feed the
    # model 10 near-duplicates), and it should not gate what retrieval *finds*. Measured
    # on the LoCoMo benchmark (1531 questions), the diversity penalty cost ~30% of recall:
    # lambda 0.5 → recall@10 0.312, lambda 1.0 → 0.443. Lower it if your workload wants a
    # spread of memories more than the single best one.
    mmr_lambda: float = Field(default=1.0, ge=0.0, le=1.0)
    # RRF weight of the graph arm relative to BM25 (1.0). The graph arm ranks by hop
    # distance, not query relevance, so at equal weight it dominates the fusion and buries
    # the real hits (LoCoMo recall@10: 0.06 at 1.0 → 0.48 at 0.1). Keep it low but > 0 so
    # graph-only memories can still be surfaced.
    rrf_graph_weight: float = Field(default=0.1, ge=0.0, le=1.0)
    # RRF weight of the vector arm relative to BM25 (1.0). The vector arm ranks by
    # proximity to the *query* embedding — topical similarity, which is only relevance
    # when the query resembles the answer. On question→statement corpora it can rank noise
    # and displace correct lexical hits: on LoCoMo, recall fell as the arm gained influence
    # (0.44 barely probing → 0.14 with an exact search). Full mode's HyDE is the intended
    # fix (it embeds a hypothetical answer, so proximity *is* relevance) — lower this only
    # if your corpus shows the arm hurting with HyDE off.
    rrf_vector_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    # RRF weight of the BM25 arm — the reference relevance ranker, so 1.0 by default. Rarely
    # changed in production; exposed so a benchmark can isolate a single arm (e.g. set it to 0
    # for a vector-only "VectorDB" baseline in the recall-vs-scale curve, HX-2).
    rrf_bm25_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    # Faiss IVF partitions (DESIGN.md §7). Keep well below the corpus size — IVF
    # trains one centroid per list, so `n_lists ≪ docs` (see `vector_train_factor`).
    vector_n_lists: int = Field(default=64, ge=1)
    # Defer index creation until the corpus is `n_lists × factor` documents, so the
    # IVF centroids train on enough points. Building at exactly `n_lists` docs yields
    # one point per centroid — badly under-trained (MA-8). Below the tier, retrieval
    # stays BM25-only (§7, §15).
    vector_train_factor: int = Field(default=40, ge=1)
    # Entity resolution (SC-1b, DESIGN §7): once a tenant accrues many entities, matching a
    # new entity against *all* of them per write is O(N) → O(N²) ingestion. A Faiss IVF index
    # on `entities.embedding` lets resolution query the top-k nearest instead. Deferred until
    # the collection warms (n_lists × train_factor), below which it full-scans (fine at small N).
    entity_vector_n_lists: int = Field(default=32, ge=1)
    entity_vector_train_factor: int = Field(default=40, ge=1)
    # How many nearest existing entities to consider as merge/flag candidates (the ANN pool).
    entity_resolution_top_k: int = Field(default=10, ge=1)
    # Graph expansion (DESIGN.md §9 stage 4): relates_to hops from seed entities (3 max).
    graph_hops: int = Field(default=2, ge=0, le=3)
    # SC-1c: cap on the `relates_to` neighbours the graph arm expands, so a hub in a dense
    # single-tenant graph can't explode the traversal (the BX-2 retrieval timeout, §23). The
    # graph arm is a down-weighted recall expander, so a bounded neighbourhood costs little.
    graph_max_neighbors: int = Field(default=200, ge=1)
    # SC-1d: cap the memories expanded per related entity. `LIMIT @max_neighbors` bounds the
    # breadth of the relates_to fan-out, but each entity's `INBOUND mentions` grows as the
    # tenant fills (a hub accrues more mentions), which drove residual retrieval scaling (§23).
    # Total graph work is then bounded to max_neighbors × this. Default 50 leaves real corpora
    # (entities mentioned by a handful of memories) untouched; only pathological hubs are capped.
    graph_max_memories_per_entity: int = Field(default=50, ge=1)
    # IN-3: cap the co-occurrence `relates_to` edges minted per turn. A turn with E entities
    # yields E(E−1)/2 pairs (an all-pairs blow-up: 20 entities → 190 edges), which is both an
    # ingestion cost and *graph noise* — a flood of low-signal `associated_with` edges is why
    # the graph arm had to be down-weighted (`rrf_graph_weight` 0.1). Bounding pairs per turn
    # cuts write cost and raises the graph's signal-to-noise. Typed relations are minted first
    # and are never dropped by this cap; only co-occurrence backfill is bounded. Default 32
    # leaves ordinary turns (≤8 entities) untouched; only dense turns are down-sampled.
    graph_max_pairs_per_turn: int = Field(default=32, ge=1)
    # Multi-hop retrieval (RQ-1, DESIGN.md §9): mode="multihop" decomposes the query into
    # independent sub-lookups, retrieves each, and re-fuses. The cap bounds the fan-out
    # (N sub-queries = N retrievals + 1 decompose call); 1 disables decomposition entirely.
    decompose_max_subqueries: int = Field(default=4, ge=1, le=8)
    # Reserved for the iterative read→retrieve→read variant; 0 = off (decomposition only).
    decompose_max_hops: int = Field(default=0, ge=0, le=2)
    # Episodic decay (DESIGN.md §11): effective_strength = strength · exp(-λ · Δdays).
    # The sweep soft-deprecates memories whose effective strength drops below floor.
    decay_lambda: float = Field(default=0.02, ge=0.0)
    decay_floor: float = Field(default=0.1, ge=0.0, le=1.0)
    # Working memory (DESIGN.md §5/§14): a `working` type with a session TTL and the
    # SCM cap — overflow beyond `working_capacity` promotes the oldest to episodic.
    working_session_ttl_seconds: int = Field(default=3600, ge=1)
    working_capacity: int = Field(default=7, ge=1)
    # GAM semantic-boundary trigger (DESIGN.md §13): on each turn, compare it to the
    # session's running topic; below the cosine threshold = a topic shift (flush the
    # working buffer + flag consolidation due). EWA blends the running topic otherwise.
    topic_shift_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    topic_ewa_alpha: float = Field(default=0.5, gt=0.0, le=1.0)
    # EWA edge weight (DESIGN.md §12): each corroboration blends in a fresh 1.0 and
    # time-decays the prior, so recently/frequently confirmed relations weigh more.
    weight_ewa_alpha: float = Field(default=0.5, gt=0.0, le=1.0)
    weight_lambda: float = Field(default=0.02, ge=0.0)
    # Dedicated embedding cache (DESIGN.md §16): memoize embed(text) per tenant so
    # recurring entity names / repeated queries skip the provider. Pure perf, safe.
    embedding_cache: bool = True
    embedding_cache_size: int = Field(default=10000, ge=1)
    # API authentication (DESIGN.md §17): a bearer key → tenant + scope map. Empty =
    # auth **off** (body-asserted ABAC, the keyless dev/CI/demo posture). When set,
    # `/v1` requires `Authorization: Bearer <key>`; tenant/scope come from the key.
    # Env (JSON): API_KEYS='{"k_abc":{"tenant_id":"acme","scope":"write"}}'.
    api_keys: dict[str, ApiKeyEntry] = Field(default_factory=dict)
    # OIDC / JWT bearer auth (DESIGN.md §17), additive to api_keys and coexisting with
    # it. Setting `oidc_issuer` enables JWT verification: `/v1` then also accepts an
    # RS256 bearer JWT, verified against the issuer's JWKS (signature + alg allowlist +
    # exp/nbf/iss/aud). Identity maps from claims: `oidc_tenant_claim` → tenant_id,
    # `oidc_scope_claim` → scope ("write" if its value contains "write", else "read").
    # Revocation is by expiry only — use short-lived tokens (no server-side denylist).
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    # JWKS endpoint; defaults to `{issuer}/.well-known/jwks.json` when unset.
    oidc_jwks_uri: str | None = None
    oidc_algorithms: tuple[str, ...] = ("RS256",)
    oidc_tenant_claim: str = "tenant_id"
    oidc_scope_claim: str = "scope"
    # Optional JWT claim (MA-7) whose value (string or list) restricts which agents the
    # token may act as — the OIDC parity of `ApiKeyEntry.agent_ids`. Unset → any agent.
    oidc_agent_claim: str | None = None
    # Clock-skew tolerance (seconds) for exp/nbf validation.
    oidc_leeway_seconds: int = Field(default=60, ge=0)
    # Durable write queue (DESIGN.md §15). "memory" = in-process (fast, zero-config —
    # the dev/CI default; loses unacked work on crash). "arango" = a durable
    # `write_intents` collection that survives restarts (set this in production).
    write_queue_backend: Literal["memory", "arango"] = "memory"
    # How long a claimed intent stays leased before it's reclaimable (crash recovery).
    write_lease_seconds: int = Field(default=60, ge=1)
    # Abuse limits (DESIGN.md §17). Request-size cap is always on (a memory turn is
    # far smaller than 1 MiB); rate limiting is opt-in (0 = off — the dev/CI default).
    max_request_bytes: int = Field(default=1_048_576, ge=1)
    rate_limit_per_minute: int = Field(default=0, ge=0)
    # Structured logging (DESIGN.md §18). "text" = human-readable (dev/CI default);
    # "json" = one JSON object per line for log pipelines (set in production).
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    # Optional shared (cross-instance) layer (DESIGN.md §16/§17). Unset = per-instance
    # (the default): the rate limiter + embedding cache live in-process. Set to a Redis
    # URL (needs the `redis` extra) to share them across instances — the limiter then
    # enforces one global budget and the embedding cache is shared. Fail-soft: a Redis
    # outage degrades to allow (limiter) / direct compute (cache), never an error.
    redis_url: str | None = None


settings = Settings()
