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
        self._client = ArangoClient(hosts=self._config.arango_url)
        self._db: StandardDatabase | None = None

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

        if cfg.arango_bearer_token:
            self._db = self._client.db(cfg.arango_db, user_token=cfg.arango_bearer_token)
        else:
            self._db = self._client.db(
                cfg.arango_db, username=cfg.arango_username, password=cfg.arango_password
            )
        return self._db

    def _sys_db(self) -> StandardDatabase:
        cfg = self._config
        if cfg.arango_bearer_token:
            return self._client.db("_system", user_token=cfg.arango_bearer_token)
        return self._client.db(
            "_system", username=cfg.arango_username, password=cfg.arango_password
        )

    def ping(self) -> bool:
        """Health check used by the API /health endpoint."""
        try:
            self.db.version()
            return True
        except Exception:
            return False
