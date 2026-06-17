"""Structured logging + correlation IDs (DESIGN.md §18, LOG)."""

from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from arango_memory.telemetry.logging import (
    JsonFormatter,
    _ContextFilter,
    request_id_var,
    tenant_var,
)


def _record(msg: str, **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("arango_memory", logging.INFO, __file__, 1, msg, None, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    _ContextFilter().filter(rec)
    return rec


# ── formatter / context (unit) ────────────────────────────
def test_json_formatter_includes_context_and_extras() -> None:
    token_r = request_id_var.set("req-123")
    token_t = tenant_var.set("acme")
    try:
        line = JsonFormatter().format(_record("stored", status=200))
    finally:
        request_id_var.reset(token_r)
        tenant_var.reset(token_t)
    obj = json.loads(line)
    assert obj["message"] == "stored"
    assert obj["request_id"] == "req-123"
    assert obj["tenant"] == "acme"
    assert obj["status"] == 200
    assert obj["level"] == "INFO"


def test_context_defaults_to_none_outside_a_request() -> None:
    obj = json.loads(JsonFormatter().format(_record("idle")))
    assert obj["request_id"] is None and obj["tenant"] is None


# ── correlation id propagation (integration) ──────────────
def test_generated_request_id_is_returned(api: TestClient) -> None:
    res = api.get("/health")
    assert res.status_code == 200
    assert res.headers.get("x-request-id")  # generated + echoed


def test_inbound_request_id_is_echoed(api: TestClient) -> None:
    res = api.get("/health", headers={"x-request-id": "trace-abc"})
    assert res.headers.get("x-request-id") == "trace-abc"
