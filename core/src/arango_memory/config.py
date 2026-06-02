"""Environment-driven settings for the memory core."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Core configuration, populated from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ArangoDB
    arango_url: str = "http://localhost:8529"
    arango_db: str = "agentic_memory"
    arango_username: str = "root"
    arango_password: str = "changeme"
    arango_bearer_token: str | None = None

    # Embeddings
    openai_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"

    # Background / extraction LLM
    anthropic_api_key: str | None = None
    background_model: str = "claude-haiku-4-5"

    # Core service
    core_host: str = "0.0.0.0"
    core_port: int = 8080

    # Enrichment mode (DESIGN.md §10)
    memory_mode: Literal["lite", "full"] = "lite"

    # Retrieval defaults (DESIGN.md §9)
    max_memory_tokens: int = Field(default=1500, ge=0)
    n_probe: int = Field(default=10, ge=1)
    k: int = Field(default=10, ge=1)


settings = Settings()
