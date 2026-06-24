"""API authentication — bearer keys, open-by-default (DESIGN.md §17, AUTH-1)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from arango_memory.config import ApiKeyEntry, settings


@pytest.fixture
def with_keys() -> Iterator[None]:
    """Enable enforced mode for one test, then restore the open default."""
    original = settings.api_keys
    settings.api_keys = {
        "k_write": ApiKeyEntry(tenant_id="tenant_a", scope="write"),
        "k_read": ApiKeyEntry(tenant_id="tenant_a", scope="read"),
        "k_other": ApiKeyEntry(tenant_id="tenant_b", scope="write"),
    }
    yield
    settings.api_keys = original


def _store(api: TestClient, headers: dict[str, str] | None = None) -> int:
    ctx = {"tenant_id": "tenant_a", "agent_id": "a", "access_level": "write"}
    res = api.post("/v1/store", json={"content": "hi", "ctx": ctx}, headers=headers or {})
    return res.status_code


# ── open mode (no keys) — unchanged behavior ──────────────
def test_open_mode_allows_unauthenticated(api: TestClient) -> None:
    assert _store(api) == 200  # no keys configured → body trusted (today's posture)


# ── enforced mode ─────────────────────────────────────────
def test_missing_key_is_401(api: TestClient, with_keys: None) -> None:
    assert _store(api) == 401


def test_unknown_key_is_401(api: TestClient, with_keys: None) -> None:
    assert _store(api, {"authorization": "Bearer nope"}) == 401


def test_valid_key_passes(api: TestClient, with_keys: None) -> None:
    assert _store(api, {"authorization": "Bearer k_write"}) == 200


def test_docs_are_exempt_in_enforced_mode(api: TestClient, with_keys: None) -> None:
    # The OpenAPI docs stay public even when keys are enforced (no creds needed).
    assert api.get("/openapi.json").status_code == 200
    assert api.get("/docs").status_code == 200


def test_health_and_ready_are_exempt(api: TestClient, with_keys: None) -> None:
    # Probes stay public even when keys are enforced (no creds needed).
    assert api.get("/health").status_code == 200
    assert api.get("/ready").status_code == 200


def test_get_endpoint_also_enforced(api: TestClient, with_keys: None) -> None:
    assert api.get("/v1/stats", params={"tenant_id": "tenant_a"}).status_code == 401
    ok = api.get(
        "/v1/stats", params={"tenant_id": "tenant_a"}, headers={"authorization": "Bearer k_write"}
    )
    assert ok.status_code == 200


# ── authz: identity derives from the key (AUTH-2) ─────────
def test_cross_tenant_write_is_403(api: TestClient, with_keys: None) -> None:
    # tenant_b's key trying to write into tenant_a (via the body) → 403.
    assert _store(api, {"authorization": "Bearer k_other"}) == 403


def test_cross_tenant_read_is_403(api: TestClient, with_keys: None) -> None:
    res = api.get(
        "/v1/stats", params={"tenant_id": "tenant_a"}, headers={"authorization": "Bearer k_other"}
    )
    assert res.status_code == 403  # tenant_b key can't read tenant_a


def test_read_scoped_key_cannot_write(api: TestClient, with_keys: None) -> None:
    assert _store(api, {"authorization": "Bearer k_read"}) == 403  # read scope, write endpoint


def test_read_scoped_key_can_read(api: TestClient, with_keys: None) -> None:
    res = api.get(
        "/v1/stats", params={"tenant_id": "tenant_a"}, headers={"authorization": "Bearer k_read"}
    )
    assert res.status_code == 200


def test_body_access_level_ignored_in_enforced_mode(api: TestClient, with_keys: None) -> None:
    # The body claims write, but the read-scoped key governs → 403 (body can't escalate).
    ctx = {"tenant_id": "tenant_a", "agent_id": "a", "access_level": "write"}
    res = api.post("/v1/store", json={"content": "x", "ctx": ctx},
                   headers={"authorization": "Bearer k_read"})
    assert res.status_code == 403


# ── authz breadth: every tenant-scoped read enforces the key's tenant ──
@pytest.mark.parametrize(
    "path, params",
    [
        ("/v1/stats", {"tenant_id": "tenant_a"}),
        ("/v1/steps", {"tenant_id": "tenant_a", "agent_id": "a"}),
        ("/v1/entities", {"tenant_id": "tenant_a"}),
        ("/v1/graph", {"tenant_id": "tenant_a"}),
    ],
)
def test_cross_tenant_read_is_403_across_endpoints(
    api: TestClient, with_keys: None, path: str, params: dict[str, str]
) -> None:
    # tenant_b's key may not read tenant_a on ANY tenant-scoped GET (data-layer scoping),
    other = api.get(path, params=params, headers={"authorization": "Bearer k_other"})
    assert other.status_code == 403, f"{path} leaked across tenants"
    # …while the owning tenant's key is admitted.
    own = api.get(path, params=params, headers={"authorization": "Bearer k_write"})
    assert own.status_code == 200, f"{path} rejected the owning tenant"
