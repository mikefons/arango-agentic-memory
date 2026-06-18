"""Shared test fixtures (DESIGN.md §22).

A single ArangoDB Enterprise container is started per test session (evaluation
mode — no license needed) and each test runs against a freshly-created, uniquely
named database for full isolation. The `--vector-index=true` flag is passed so
vector-index tests can run once they land in later steps.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator, Sequence

import pytest
from arango import ArangoClient
from arango.database import StandardDatabase
from fastapi.testclient import TestClient
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from arango_memory.api.app import create_app
from arango_memory.client import ArangoMemoryClient
from arango_memory.config import Settings
from arango_memory.retrieve.search import RetrieveResult, retrieve
from arango_memory.schema.collections import ensure_schema

ARANGO_IMAGE = "arangodb/enterprise:3.12.9.1"
ROOT_PASSWORD = "testpassword"  # noqa: S105 — throwaway container credential
STARTUP_TIMEOUT = 180


class StubEmbedder:
    """Maps known strings to fixed vectors so cosine similarity is controllable.

    Lets tests assert threshold-band behavior (entity merge/flag, topic shift) on
    explicit geometry instead of FakeEmbedder's incidental token-hash cosine.
    Unknown text → a fixed default vector.
    """

    model = "stub"
    version = "1"
    dimensions = 3

    def __init__(self, table: dict[str, list[float]], default: list[float] | None = None) -> None:
        self._table = table
        self._default = default if default is not None else [0.0, 0.0, 1.0]

    def embed(self, text: str) -> list[float]:
        return self._table.get(text, self._default)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


@pytest.fixture(scope="session")
def arango_url() -> Iterator[str]:
    """Start one Enterprise container for the whole session; yield its base URL."""
    container = (
        DockerContainer(ARANGO_IMAGE)
        .with_env("ARANGO_ROOT_PASSWORD", ROOT_PASSWORD)
        .with_command("arangod --vector-index=true")
        .with_exposed_ports(8529)
    )
    container.start()
    try:
        wait_for_logs(container, "is ready for business", timeout=STARTUP_TIMEOUT)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(8529)
        yield f"http://{host}:{port}"
    finally:
        container.stop()


@pytest.fixture
def settings(arango_url: str) -> Settings:
    """Per-test settings pointing at a uniquely named database."""
    return Settings(
        arango_url=arango_url,
        arango_db=f"test_{uuid.uuid4().hex[:8]}",
        arango_username="root",
        arango_password=ROOT_PASSWORD,
        arango_tls_verify=False,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[ArangoMemoryClient]:
    """Connected client bound to the per-test database; drops it on teardown."""
    mem_client = ArangoMemoryClient(config=settings)
    mem_client.connect()
    yield mem_client

    teardown = ArangoClient(hosts=settings.arango_url)
    teardown.db("_system", username="root", password=ROOT_PASSWORD).delete_database(
        settings.arango_db, ignore_missing=True
    )


@pytest.fixture
def db(client: ArangoMemoryClient) -> StandardDatabase:
    """Connected database with the schema bootstrapped."""
    database = client.db
    ensure_schema(database)
    return database


@pytest.fixture
def ctx() -> dict[str, str]:
    """A default tenant/agent context for store/retrieve calls."""
    return {"tenant_id": "tenant_a", "agent_id": "agent_1"}


@pytest.fixture
def api(client: ArangoMemoryClient) -> Iterator[TestClient]:
    """A TestClient over the factory app (runs the lifespan + write worker)."""
    with TestClient(create_app(client)) as test_client:
        yield test_client


@pytest.fixture
def wait_for_searchable() -> Callable[..., RetrieveResult]:
    """Poll retrieval until the ArangoSearch view reflects a write (eventual consistency)."""

    def _wait(
        database: StandardDatabase,
        *,
        query: str,
        tenant_id: str,
        agent_id: str,
        attempts: int = 20,
        delay: float = 0.25,
    ) -> RetrieveResult:
        result = retrieve(database, query=query, tenant_id=tenant_id, agent_id=agent_id)
        for _ in range(attempts):
            if result.hits:
                return result
            time.sleep(delay)
            result = retrieve(database, query=query, tenant_id=tenant_id, agent_id=agent_id)
        return result

    return _wait
