"""Abuse limits — request-size cap + rate limiting (DESIGN.md §17, RL-1/RL-2)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from arango_memory.api.limits import RateLimiter
from arango_memory.config import settings


@pytest.fixture
def small_cap() -> Iterator[None]:
    """Shrink the body cap so a normal-looking request trips it."""
    original = settings.max_request_bytes
    settings.max_request_bytes = 200
    yield
    settings.max_request_bytes = original


def test_oversized_body_is_413(api: TestClient, small_cap: None) -> None:
    ctx = {"tenant_id": "t", "agent_id": "a", "access_level": "write"}
    big = {"content": "x" * 500, "ctx": ctx}
    assert api.post("/v1/store", json=big).status_code == 413


def test_normal_body_passes(api: TestClient, small_cap: None) -> None:
    ctx = {"tenant_id": "t", "agent_id": "a", "access_level": "write"}
    assert api.post("/v1/store", json={"content": "hi", "ctx": ctx}).status_code == 200


def test_health_unaffected(api: TestClient, small_cap: None) -> None:
    assert api.get("/health").status_code == 200


# ── rate limiting (RL-2) ──────────────────────────────────
def test_rate_limiter_unit() -> None:
    rl = RateLimiter()
    assert rl.check("k", limit=2) is None  # 1
    assert rl.check("k", limit=2) is None  # 2
    assert rl.check("k", limit=2) is not None  # 3 → over, returns retry-after
    assert rl.check("other", limit=2) is None  # separate key has its own budget


def test_rate_limiter_disabled_is_noop() -> None:
    rl = RateLimiter()
    assert all(rl.check("k", limit=0) is None for _ in range(100))


@pytest.fixture
def rate_two() -> Iterator[None]:
    from arango_memory.api.limits import _rate_limiter

    original = settings.rate_limit_per_minute
    settings.rate_limit_per_minute = 2
    _rate_limiter._hits.clear()  # isolate from other tests sharing the singleton
    yield
    settings.rate_limit_per_minute = original
    _rate_limiter._hits.clear()


def test_rate_limit_429_after_budget(api: TestClient, rate_two: None) -> None:
    codes = [api.get("/v1/stats", params={"tenant_id": "t"}).status_code for _ in range(3)]
    assert codes[:2] == [200, 200]
    assert codes[2] == 429  # third within the window is throttled


def test_rate_limit_disabled_by_default(api: TestClient) -> None:
    # No rate_two fixture → rate_limit_per_minute is 0 → never throttles.
    codes = [api.get("/v1/stats", params={"tenant_id": "t"}).status_code for _ in range(5)]
    assert all(c == 200 for c in codes)


def test_health_exempt_from_rate_limit(api: TestClient, rate_two: None) -> None:
    assert all(api.get("/health").status_code == 200 for _ in range(5))
