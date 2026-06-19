"""Abuse limits (DESIGN.md §17): request-size cap + per-tenant rate limiting.

- **Size cap** — a pure-ASGI middleware that rejects an oversized body via its
  `Content-Length` *before* the server buffers it (the DoS guard). Always on.
- **Rate limit** — a FastAPI dependency (so it runs after auth and can key off the
  authenticated tenant; IP in open mode). In-process fixed-window; opt-in. Lands in
  AUTH/RL-2.
"""

from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import settings
from ..telemetry.logging import logger

# Public paths exempt from rate limiting (liveness + API docs).
_EXEMPT_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})
_WINDOW_SECONDS = 60.0


class RequestSizeLimitMiddleware:
    """Reject requests whose declared `Content-Length` exceeds the cap (413).

    The cap is read from `settings.max_request_bytes` per request, so it stays in
    sync with config (and is overridable in tests).
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            length = self._content_length(scope)
            if length is not None and length > settings.max_request_bytes:
                response = JSONResponse(
                    {"detail": f"request body exceeds {settings.max_request_bytes} bytes"},
                    status_code=413,
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for key, value in scope.get("headers", []):
            if key == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return None
        return None


class RateLimiter:
    """In-process fixed-window rate limiter, keyed per caller (tenant or IP).

    Single-process — the limit is per instance, so N instances allow N×limit; a
    shared (Redis) limiter is the distributed answer (roadmap). `check` returns the
    seconds until the window resets when the key is over budget, else None.
    """

    def __init__(self) -> None:
        self._hits: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window: float = _WINDOW_SECONDS) -> float | None:
        if limit <= 0:
            return None  # disabled
        now = time.monotonic()
        with self._lock:
            start, count = self._hits.get(key, (now, 0))
            if now - start >= window:  # window elapsed → reset
                start, count = now, 0
            count += 1
            self._hits[key] = (start, count)
            return window - (now - start) if count > limit else None


class RedisRateLimiter:
    """Cross-instance fixed-window limiter backed by Redis (DESIGN.md §17).

    One global budget across all instances (vs the per-instance `RateLimiter`).
    **Fail-open:** any Redis error allows the request — an abuse limiter must never
    take the service down. `INCR` + `EXPIRE NX` keeps a single window TTL per key.
    """

    def check(self, key: str, *, limit: int, window: float = _WINDOW_SECONDS) -> float | None:
        if limit <= 0:
            return None
        try:
            from ..redis_client import get_redis

            redis_key = f"ratelimit:{key}"
            pipe = get_redis().pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, int(window), nx=True)  # set the window TTL once
            pipe.ttl(redis_key)
            count, _, ttl = pipe.execute()
        except Exception:  # noqa: BLE001 — §15 fail-open: never break the turn on a Redis fault
            logger.warning("rate limiter degraded (redis error); allowing", extra={"key": key})
            return None
        if int(count) <= limit:
            return None
        return float(ttl) if ttl and int(ttl) > 0 else window


_rate_limiter = RateLimiter()
_redis_rate_limiter = RedisRateLimiter()


def _active_limiter() -> RateLimiter | RedisRateLimiter:
    """Redis-backed limiter when `REDIS_URL` is set (shared budget), else in-process."""
    return _redis_rate_limiter if settings.redis_url else _rate_limiter


def rate_limit(request: Request) -> None:
    """Per-tenant (or per-IP, open mode) rate limit (§17). No-op when disabled.

    Runs after `require_principal`, so it keys off the authenticated tenant when
    present; otherwise the client IP (best-effort for the open/keyless posture).
    """
    if settings.rate_limit_per_minute <= 0 or request.url.path in _EXEMPT_PATHS:
        return
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        key = f"tenant:{principal.tenant_id}"
    else:
        client = request.client
        key = f"ip:{client.host if client else 'unknown'}"
    retry_after = _active_limiter().check(key, limit=settings.rate_limit_per_minute)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


__all__ = ["RateLimiter", "RequestSizeLimitMiddleware", "rate_limit"]
