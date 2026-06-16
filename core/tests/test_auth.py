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
    settings.api_keys = {"k_write": ApiKeyEntry(tenant_id="tenant_a", scope="write")}
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


def test_health_is_exempt(api: TestClient, with_keys: None) -> None:
    assert api.get("/health").status_code == 200  # always public, no key needed


def test_get_endpoint_also_enforced(api: TestClient, with_keys: None) -> None:
    assert api.get("/v1/stats", params={"tenant_id": "tenant_a"}).status_code == 401
    ok = api.get(
        "/v1/stats", params={"tenant_id": "tenant_a"}, headers={"authorization": "Bearer k_write"}
    )
    assert ok.status_code == 200
