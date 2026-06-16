"""Environment-driven settings for the memory core."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Retrieval defaults (DESIGN.md §9)
    max_memory_tokens: int = Field(default=1500, ge=0)
    n_probe: int = Field(default=10, ge=1)
    k: int = Field(default=10, ge=1)
    # Faiss IVF training tier (DESIGN.md §7): index trains once corpus ≥ n_lists.
    vector_n_lists: int = Field(default=256, ge=1)
    # Graph expansion (DESIGN.md §9 stage 4): relates_to hops from seed entities (3 max).
    graph_hops: int = Field(default=2, ge=0, le=3)
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


settings = Settings()
