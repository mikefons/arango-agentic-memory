"""FastMCP server exposing the core as MCP tools (DESIGN.md §21).

A thin wrapper over the core's /v1 HTTP API for Claude Desktop / Cursor /
Windsurf. Run with `python -m arango_memory.mcp` (stdio). The core URL comes
from `ARANGO_MEMORY_CORE_URL` (default http://localhost:8080).

Exposes the full §19 surface as 9 tools (store/search/record_step/list_steps/
forget/stats/get_entity/list_entities/seed).
"""

from __future__ import annotations

import os
from typing import Any, cast

import httpx
from mcp.server.fastmcp import FastMCP

from . import tools
from .tools import CoreClient


def build_server(client: CoreClient | None = None) -> FastMCP:
    """Build the MCP server. Tests inject a client; production uses httpx over HTTP."""
    base_url = os.environ.get("ARANGO_MEMORY_CORE_URL", "http://localhost:8080")
    # Bearer key when the core enforces auth (§17); omitted when it runs open (keyless).
    api_key = os.environ.get("ARANGO_MEMORY_API_KEY")
    headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
    http = client or cast(
        CoreClient, httpx.Client(base_url=base_url, timeout=30.0, headers=headers)
    )
    server = FastMCP("arango-memory")

    @server.tool()
    def store(content: str, tenant_id: str, agent_id: str) -> Any:
        """Store one turn of memory for a tenant/agent."""
        return tools.store_memory(http, content=content, tenant_id=tenant_id, agent_id=agent_id)

    @server.tool()
    def search(query: str, tenant_id: str, agent_id: str, mode: str = "lite") -> Any:
        """Retrieve relevant memories for a query (assembled context + hits)."""
        return tools.search_memory(
            http, query=query, tenant_id=tenant_id, agent_id=agent_id, mode=mode
        )

    @server.tool()
    def record_step(
        tool_name: str, arguments: dict[str, Any], outcome: str, tenant_id: str, agent_id: str
    ) -> Any:
        """Record a completed tool call as procedural memory."""
        return tools.record_step(
            http,
            tool_name=tool_name,
            arguments=arguments,
            outcome=outcome,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )

    @server.tool()
    def list_steps(tenant_id: str, agent_id: str, tool_name: str | None = None) -> Any:
        """List recorded procedural memories (tool traces)."""
        return tools.list_steps(http, tenant_id=tenant_id, agent_id=agent_id, tool_name=tool_name)

    @server.tool()
    def forget(tenant_id: str, agent_id: str | None = None) -> Any:
        """Right to be forgotten: soft-delete a tenant's (or one agent's) memories."""
        return tools.forget_memory(http, tenant_id=tenant_id, agent_id=agent_id)

    @server.tool()
    def stats(tenant_id: str) -> Any:
        """Per-tenant graph health counts."""
        return tools.graph_stats(http, tenant_id=tenant_id)

    @server.tool()
    def get_entity(entity_id: str, tenant_id: str) -> Any:
        """Fetch a semantic entity (by id) plus its related entities."""
        return tools.get_entity(http, entity_id=entity_id, tenant_id=tenant_id)

    @server.tool()
    def list_entities(tenant_id: str, agent_id: str | None = None, label: str | None = None) -> Any:
        """List a tenant's semantic entities (optionally filtered by agent/label)."""
        return tools.list_entities(http, tenant_id=tenant_id, agent_id=agent_id, label=label)

    @server.tool()
    def seed(profile: dict[str, Any], tenant_id: str, agent_id: str) -> Any:
        """Cold-start seed: pre-populate semantic memory from a profile."""
        return tools.seed_profile(http, profile=profile, tenant_id=tenant_id, agent_id=agent_id)

    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
