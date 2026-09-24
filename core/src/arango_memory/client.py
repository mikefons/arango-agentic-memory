"""ArangoDB client + connection abstraction.

Works against both self-hosted/Docker and ArangoGraph cloud via the same
connection settings (DESIGN.md §6). Authentication falls back from bearer
token (ArangoGraph) to basic auth (local).
"""

from __future__ import annotations

from arango import ArangoClient  # type: ignore[attr-defined]
from arango.database import StandardDatabase

from .config import Settings, settings


class ArangoMemoryClient:
    """Thin wrapper around python-arango exposing the memory database handle."""

    def __init__(self, config: Settings | None = None) -> None:
        self._config = config or settings
        # verify_override controls TLS verification for HTTPS (ArangoGraph).
        # Harmless for plain-http local connections.
        self._client = ArangoClient(
            hosts=self._config.arango_url,
            verify_override=self._config.arango_tls_verify,
            request_timeout=self._config.arango_request_timeout,
        )
        self._db: StandardDatabase | None = None

    @property
    def config(self) -> Settings:
        return self._config

    @property
    def db(self) -> StandardDatabase:
        if self._db is None:
            raise RuntimeError("Client not connected; call connect() first.")
        return self._db

    def connect(self) -> StandardDatabase:
        """Connect to (and lazily create) the memory database."""
        cfg = self._config
        sys_db = self._sys_db()
        if not sys_db.has_database(cfg.arango_db):
            sys_db.create_database(cfg.arango_db)
        self._db = self.database(cfg.arango_db)
        return self._db

    def database(self, name: str) -> StandardDatabase:
        """A handle on any database on the server, with the configured credentials."""
        cfg = self._config
        if cfg.arango_bearer_token:
            return self._client.db(name, user_token=cfg.arango_bearer_token)
        return self._client.db(name, username=cfg.arango_username, password=cfg.arango_password)

    def _sys_db(self) -> StandardDatabase:
        return self.database("_system")

    def ping(self) -> bool:
        """Health check used by the API /health endpoint."""
        try:
            self.db.version()
            return True
        except Exception:
            return False

    def describe(self) -> dict[str, str]:
        """Connection metadata for diagnostics (no secrets)."""
        return {
            "target": self._config.arango_target,
            "url": self._config.arango_url,
            "database": self._config.arango_db,
            "auth": "bearer_token" if self._config.arango_bearer_token else "basic",
            "tls_verify": str(self._config.arango_tls_verify),
        }
