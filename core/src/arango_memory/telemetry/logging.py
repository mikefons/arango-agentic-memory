"""Structured logging + correlation IDs (DESIGN.md §18).

Stdlib `logging` (no new dependency) with a JSON-lines formatter for production log
pipelines or human-readable text for dev. Every record carries a **request_id** (a
correlation id) and the **tenant**, pulled from contextvars set per request by the
middleware — so a turn's request log, dead-letter, and degraded-retrieve lines all
share one id. Configured once at startup via `configure_logging()`.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import settings

#: Correlation id + tenant for the in-flight request (set by the logging middleware).
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_var: ContextVar[str | None] = ContextVar("tenant", default=None)

logger = logging.getLogger("arango_memory")

# Standard LogRecord attributes — anything else on the record is treated as a
# structured "extra" field and included in JSON output.
_RESERVED = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


class _ContextFilter(logging.Filter):
    """Attach the request-scoped correlation id + tenant to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.tenant = tenant_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: level, logger, message, request_id, tenant, + extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "tenant": getattr(record, "tenant", None),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable: `LEVEL logger [request_id tenant] message  key=val …`."""

    def format(self, record: logging.LogRecord) -> str:
        rid = getattr(record, "request_id", None) or "-"
        tenant = getattr(record, "tenant", None) or "-"
        extras = " ".join(
            f"{k}={v}" for k, v in record.__dict__.items()
            if k not in _RESERVED and k not in {"request_id", "tenant"}
        )
        base = f"{record.levelname:<5} {record.name} [{rid} {tenant}] {record.getMessage()}"
        return f"{base}  {extras}" if extras else base


def configure_logging() -> None:
    """Install the formatter + context filter on the `arango_memory` logger. Idempotent."""
    formatter: logging.Formatter = (
        JsonFormatter() if settings.log_format == "json" else TextFormatter()
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(_ContextFilter())

    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(settings.log_level.upper())
    logger.propagate = False


def _inbound_request_id(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key == b"x-request-id" and value:
            decoded: str = bytes(value).decode("latin-1")
            return decoded[:128]
    return None


class RequestLogMiddleware:
    """Assign/propagate a correlation id and emit one structured access line/request.

    Reads `X-Request-ID` (or generates one), holds it in `request_id_var` for the
    duration, echoes it in the response header, and logs method/path/status/
    duration_ms/tenant once the response starts. `/health` is not access-logged
    (liveness noise) but still gets a correlation id.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        rid = _inbound_request_id(scope) or uuid.uuid4().hex
        rid_token = request_id_var.set(rid)
        tenant_token = tenant_var.set(None)
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", rid.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            if scope.get("path") != "/health":
                logger.info(
                    "request",
                    extra={
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "status": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
            request_id_var.reset(rid_token)
            tenant_var.reset(tenant_token)
