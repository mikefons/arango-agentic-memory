"""Optional Redis-backed rate limiter (DESIGN.md §17). Uses fakeredis — no server."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import fakeredis
import pytest
from fastapi import HTTPException

import arango_memory.redis_client as redis_client
from arango_memory.api.limits import (
    RateLimiter,
    RedisRateLimiter,
    _active_limiter,
    rate_limit,
)
from arango_memory.config import settings


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[fakeredis.FakeRedis]:
    """Enable Redis mode + back get_redis with one shared in-memory fake."""
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(settings, "redis_url", "redis://fake")
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    yield fake


def test_active_limiter_selects_by_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "redis_url", None)
    assert isinstance(_active_limiter(), RateLimiter)
    monkeypatch.setattr(settings, "redis_url", "redis://fake")
    assert isinstance(_active_limiter(), RedisRateLimiter)


def test_redis_limiter_enforces_one_budget_across_instances(fake_redis: Any) -> None:
    # Two limiter objects model two API instances sharing one Redis budget.
    a, b = RedisRateLimiter(), RedisRateLimiter()
    assert a.check("tenant:acme", limit=3, window=60) is None  # 1
    assert b.check("tenant:acme", limit=3, window=60) is None  # 2 (other instance)
    assert a.check("tenant:acme", limit=3, window=60) is None  # 3
    over = b.check("tenant:acme", limit=3, window=60)          # 4 → over the shared cap
    assert over is not None and over > 0


def test_redis_limiter_isolates_keys(fake_redis: Any) -> None:
    limiter = RedisRateLimiter()
    assert limiter.check("tenant:a", limit=1, window=60) is None
    assert limiter.check("tenant:a", limit=1, window=60) is not None  # a over budget
    assert limiter.check("tenant:b", limit=1, window=60) is None      # b unaffected


def test_redis_limiter_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "redis_url", "redis://fake")

    def boom() -> Any:
        raise RuntimeError("redis down")

    monkeypatch.setattr(redis_client, "get_redis", boom)
    # A Redis outage must allow the request, never raise (abuse limiter ≠ availability risk).
    assert RedisRateLimiter().check("tenant:acme", limit=1, window=60) is None


def _request(tenant: str = "acme") -> Any:
    state = SimpleNamespace(principal=SimpleNamespace(tenant_id=tenant, scope="write"))
    return SimpleNamespace(url=SimpleNamespace(path="/v1/store"), state=state, client=None)


def test_rate_limit_dependency_429s_over_shared_budget(
    fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    rate_limit(_request())  # 1
    rate_limit(_request())  # 2
    with pytest.raises(HTTPException) as exc:
        rate_limit(_request())  # 3 → 429
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers
