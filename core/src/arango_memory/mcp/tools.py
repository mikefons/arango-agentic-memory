"""MCP tool logic — thin wrappers over the core's /v1 HTTP API (DESIGN.md §21).

No `mcp` import here, so the logic is testable against the core via a TestClient.
The client is any object with httpx-style `post`/`get` (FastMCP server uses a real
`httpx.Client`; tests pass a FastAPI `TestClient`).
"""

from __future__ import annotations

from typing import Any, Protocol


class _Response(Protocol):
    def json(self) -> Any: ...


class CoreClient(Protocol):
    def post(self, url: str, *, json: Any) -> _Response: ...
    def get(self, url: str, *, params: dict[str, Any]) -> _Response: ...


def store_memory(client: CoreClient, *, content: str, tenant_id: str, agent_id: str) -> Any:
    """Store one turn of memory."""
    ctx = {"tenant_id": tenant_id, "agent_id": agent_id, "access_level": "write"}
    return client.post("/v1/store", json={"content": content, "ctx": ctx}).json()


def search_memory(
    client: CoreClient, *, query: str, tenant_id: str, agent_id: str, mode: str = "lite"
) -> Any:
    """Retrieve relevant memories for a query (assembled context + hits)."""
    ctx = {"tenant_id": tenant_id, "agent_id": agent_id, "access_level": "read"}
    return client.post(
        "/v1/retrieve", json={"query": query, "ctx": ctx, "opts": {"mode": mode}}
    ).json()


def record_step(
    client: CoreClient,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    outcome: str,
    tenant_id: str,
    agent_id: str,
) -> Any:
    """Record a completed tool call as procedural memory."""
    ctx = {"tenant_id": tenant_id, "agent_id": agent_id, "access_level": "write"}
    body = {"tool_name": tool_name, "arguments": arguments, "outcome": outcome, "ctx": ctx}
    return client.post("/v1/step", json=body).json()


def list_steps(
    client: CoreClient, *, tenant_id: str, agent_id: str, tool_name: str | None = None
) -> Any:
    """List recorded procedural memories (tool traces)."""
    params: dict[str, Any] = {"tenant_id": tenant_id, "agent_id": agent_id}
    if tool_name is not None:
        params["tool_name"] = tool_name
    return client.get("/v1/steps", params=params).json()


def forget_memory(client: CoreClient, *, tenant_id: str, agent_id: str | None = None) -> Any:
    """Right to be forgotten: soft-delete a tenant's (or one agent's) memories."""
    body = {"tenant_id": tenant_id, "agent_id": agent_id, "access_level": "write"}
    return client.post("/v1/forget", json=body).json()


def graph_stats(client: CoreClient, *, tenant_id: str) -> Any:
    """Per-tenant graph health counts."""
    return client.get("/v1/stats", params={"tenant_id": tenant_id}).json()


def get_entity(client: CoreClient, *, entity_id: str, tenant_id: str) -> Any:
    """Fetch a semantic entity (by id) plus its related entities."""
    return client.get("/v1/entity", params={"entity_id": entity_id, "tenant_id": tenant_id}).json()


def list_entities(
    client: CoreClient, *, tenant_id: str, agent_id: str | None = None, label: str | None = None
) -> Any:
    """List a tenant's semantic entities (optionally filtered by agent/label)."""
    params: dict[str, Any] = {"tenant_id": tenant_id}
    if agent_id is not None:
        params["agent_id"] = agent_id
    if label is not None:
        params["label"] = label
    return client.get("/v1/entities", params=params).json()


def seed_profile(
    client: CoreClient, *, profile: dict[str, Any], tenant_id: str, agent_id: str
) -> Any:
    """Cold-start seed: pre-populate semantic memory from a profile."""
    ctx = {"tenant_id": tenant_id, "agent_id": agent_id, "access_level": "write"}
    return client.post("/v1/seed", json={"profile": profile, "ctx": ctx}).json()
