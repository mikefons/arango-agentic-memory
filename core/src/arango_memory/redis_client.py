"""Lazy Redis client for the optional shared layer (DESIGN.md §16/§17).

Enabled by `settings.redis_url`; needs the `redis` extra. The client is built once
and reused (redis-py pools connections internally). Callers that use Redis must be
**fail-soft** — a Redis outage should degrade (limiter → allow, cache → miss), never
raise into a request — so this only constructs the client; error handling lives at
the call sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import settings

if TYPE_CHECKING:
    from redis import Redis

_client: Redis | None = None
_built_for: str | None = None


def redis_enabled() -> bool:
    return settings.redis_url is not None


def get_redis() -> Redis:
    """The shared Redis client (cached). Raises if `redis_url` is unset or the
    `redis` extra is missing (a config error, surfaced at startup/first use)."""
    global _client, _built_for
    url = settings.redis_url
    if url is None:
        raise RuntimeError("redis_url is not configured")
    if _client is None or _built_for != url:
        try:
            import redis
        except ModuleNotFoundError as exc:  # pragma: no cover — env-dependent
            raise RuntimeError(
                "REDIS_URL is set but the 'redis' extra is not installed "
                "(pip install 'arango-memory[redis]')"
            ) from exc
        _client = redis.Redis.from_url(url)
        _built_for = url
    return _client


def reset_redis() -> None:
    """Drop the cached client (tests swap the URL / inject a fake)."""
    global _client, _built_for
    _client = None
    _built_for = None
