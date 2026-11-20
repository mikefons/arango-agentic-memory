"""Delegate credentials (MCP-1): a trusted service acting on behalf of a caller it verified.

The MCP server in OAuth mode holds one delegate key and asserts each caller's identity in
`X-On-Behalf-Of-*` headers. These tests pin the fail-closed rules: a delegate must assert, may
only assert tenants/agents its key covers, never widens scope, and nobody else may assert.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from arango_memory.config import ApiKeyEntry, settings


@pytest.fixture
def with_delegates() -> Iterator[None]:
    original = settings.api_keys
    settings.api_keys = {
        "d_any": ApiKeyEntry(tenant_id="*", scope="write", delegate=True),
        "d_t1_read": ApiKeyEntry(tenant_id="t1", scope="read", delegate=True),
        "d_crew": ApiKeyEntry(tenant_id="*", scope="write", delegate=True,
                              agent_ids=["research::*"]),
        "k_plain": ApiKeyEntry(tenant_id="t1", scope="write"),
    }
    yield
    settings.api_keys = original


def _store(api: TestClient, key: str, *, tenant: str = "t1", agent: str = "a",
           obo: dict[str, str] | None = None) -> int:
    ctx = {"tenant_id": tenant, "agent_id": agent, "access_level": "write"}
    headers = {"authorization": f"Bearer {key}", **{f"x-on-behalf-of-{k}": v
                                                     for k, v in (obo or {}).items()}}
    return api.post("/v1/store", json={"content": "hi", "ctx": ctx}, headers=headers).status_code


def test_delegate_acts_as_the_asserted_tenant(api: TestClient, with_delegates: None) -> None:
    assert _store(api, "d_any", tenant="t1", obo={"tenant": "t1", "scope": "write"}) == 200
    assert _store(api, "d_any", tenant="t2", obo={"tenant": "t2", "scope": "write"}) == 200
    # The body's tenant must still match the asserted one (normal ABAC applies).
    assert _store(api, "d_any", tenant="t2", obo={"tenant": "t1", "scope": "write"}) == 403


def test_delegate_must_assert_an_identity(api: TestClient, with_delegates: None) -> None:
    assert _store(api, "d_any") == 403


def test_tenant_bound_delegate_cannot_assert_other_tenants(
    api: TestClient, with_delegates: None
) -> None:
    assert _store(api, "d_t1_read", tenant="t2", obo={"tenant": "t2"}) == 403


def test_scope_is_capped_by_the_delegate_and_defaults_to_read(
    api: TestClient, with_delegates: None
) -> None:
    # Asserting write through a read-capped delegate yields read → the write is refused.
    assert _store(api, "d_t1_read", obo={"tenant": "t1", "scope": "write"}) == 403
    # No asserted scope → least privilege (read).
    assert _store(api, "d_any", obo={"tenant": "t1"}) == 403
    assert _store(api, "d_any", obo={"tenant": "t1", "scope": "admin"}) == 403  # invalid


def test_agents_are_capped_by_the_delegate(api: TestClient, with_delegates: None) -> None:
    write = {"tenant": "t1", "scope": "write"}
    assert _store(api, "d_crew", agent="research::query",
                  obo={**write, "agents": "research::query"}) == 200
    assert _store(api, "d_crew", agent="writer", obo={**write, "agents": "writer"}) == 403
    # Nothing asserted → the delegate's own agent cap applies.
    assert _store(api, "d_crew", agent="writer", obo=write) == 403
    assert _store(api, "d_crew", agent="research::insight", obo=write) == 403  # consolidate-only


def test_non_delegate_key_cannot_assert(api: TestClient, with_delegates: None) -> None:
    assert _store(api, "k_plain", obo={"tenant": "t1", "scope": "write"}) == 403
    assert _store(api, "k_plain") == 200  # same key, no assertion → normal behaviour


def test_wildcard_tenant_requires_a_delegate_key() -> None:
    with pytest.raises(ValidationError):
        ApiKeyEntry(tenant_id="*", scope="write")
