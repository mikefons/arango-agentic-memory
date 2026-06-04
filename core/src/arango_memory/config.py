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

    # Enrichment mode (DESIGN.md §10)
    memory_mode: Literal["lite", "full"] = "lite"

    # Retrieval defaults (DESIGN.md §9)
    max_memory_tokens: int = Field(default=1500, ge=0)
    n_probe: int = Field(default=10, ge=1)
    k: int = Field(default=10, ge=1)
    # Faiss IVF training tier (DESIGN.md §7): index trains once corpus ≥ n_lists.
    vector_n_lists: int = Field(default=256, ge=1)


settings = Settings()
