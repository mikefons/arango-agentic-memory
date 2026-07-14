"""Per-agent key binding + insight-tier write protection (DESIGN.md §17, MA-7)."""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arango_memory.api.app import _authorize
from arango_memory.config import ApiKeyEntry, scope_allows, settings
from arango_memory.security.auth import Principal, agent_allowed


# ── pure helpers ──────────────────────────────────────────
def test_scope_allows_is_ordered() -> None:
    assert scope_allows("consolidate", "write") is True
    assert scope_allows("consolidate", "consolidate") is True
    assert scope_allows("write", "write") is True
    assert scope_allows("write", "consolidate") is False
    assert scope_allows("read", "write") is False


def test_agent_allowed_exact_glob_and_any() -> None:
    assert agent_allowed(None, "anything") is True  # unbound → any
    assert agent_allowed(("hero-1",), "hero-1") is True
    assert agent_allowed(("hero-1",), "hero-2") is False
    assert agent_allowed(("research::*",), "research::query") is True
    assert agent_allowed(("research::*",), "research::insight") is True
    assert agent_allowed(("research::*",), "writer::query") is False


def _req_with(principal: Principal | None) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(principal=principal))


def _authorize_isolated(*args: object, **kwargs: object) -> object:
    # `_authorize` sets the `tenant_var` contextvar; run it in a copied context so the
    # set doesn't leak across tests.
    return contextvars.copy_context().run(lambda: _authorize(*args, **kwargs))  # type: ignore[arg-type]


def test_authorize_filters_read_agents_to_the_allowlist() -> None:
    req = _req_with(Principal(tenant_id="t", scope="read", agent_ids=("hero-1", "guild::*")))
    got = _authorize_isolated(
        req, tenant_id="t", read_agent_ids=["hero-1", "hero-2", "guild::query"]
    )
    assert got == ["hero-1", "guild::query"]  # hero-2 silently dropped, request not failed


def test_authorize_passes_reads_through_for_unbound_key() -> None:
    req = _req_with(Principal(tenant_id="t", scope="read", agent_ids=None))
    got = _authorize_isolated(req, tenant_id="t", read_agent_ids=["a", "b"])
    assert got == ["a", "b"]


# ── API-level enforcement ─────────────────────────────────
@pytest.fixture
def with_agent_keys() -> Iterator[None]:
    original = settings.api_keys
    settings.api_keys = {
        "k_hero1": ApiKeyEntry(tenant_id="t", scope="write", agent_ids=["hero-1"]),
        "k_any": ApiKeyEntry(tenant_id="t", scope="write"),  # unbound (regression)
        "k_writer": ApiKeyEntry(tenant_id="t", scope="write", agent_ids=["crew::*"]),
        "k_consolidate": ApiKeyEntry(tenant_id="t", scope="consolidate", agent_ids=["crew::*"]),
    }
    yield
    settings.api_keys = original


def _store(api: TestClient, key: str, agent_id: str) -> int:
    ctx = {"tenant_id": "t", "agent_id": agent_id, "access_level": "write"}
    res = api.post(
        "/v1/store", json={"content": "hi", "ctx": ctx}, headers={"authorization": f"Bearer {key}"}
    )
    return res.status_code


def test_bound_key_writes_its_own_agent(api: TestClient, with_agent_keys: None) -> None:
    assert _store(api, "k_hero1", "hero-1") == 200


def test_bound_key_cannot_impersonate_another_agent(
    api: TestClient, with_agent_keys: None
) -> None:
    assert _store(api, "k_hero1", "hero-2") == 403


def test_unbound_key_writes_any_agent(api: TestClient, with_agent_keys: None) -> None:
    assert _store(api, "k_any", "whoever") == 200  # regression: agent_ids=None unchanged


def test_insight_write_needs_consolidate_scope(api: TestClient, with_agent_keys: None) -> None:
    # A plain write-scoped crew key may write the query tier but not ::insight.
    assert _store(api, "k_writer", "crew::query") == 200
    assert _store(api, "k_writer", "crew::insight") == 403
    # The consolidate-scoped key may write the insight tier.
    assert _store(api, "k_consolidate", "crew::insight") == 200


def test_open_mode_unchanged_by_agent_binding(api: TestClient) -> None:
    # No keys configured → body trusted, no agent/insight enforcement (today's posture).
    ctx = {"tenant_id": "t", "agent_id": "crew::insight", "access_level": "write"}
    assert api.post("/v1/store", json={"content": "hi", "ctx": ctx}).status_code == 200
