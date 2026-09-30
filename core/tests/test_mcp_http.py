"""MCP over Streamable HTTP: a real uvicorn server + the SDK's HTTP client (DESIGN.md §21).

Pass-through auth is the point: each caller's bearer credential reaches the core, so the core's
ABAC decides — proven with a tenant-bound key that the core rejects for another tenant.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from arango_memory.config import ApiKeyEntry, settings
from arango_memory.mcp.server import build_http_app, build_server, main

EXPECTED_TOOLS = {
    "store", "search", "prime", "flush", "record_step", "list_steps", "forget", "stats",
    "get_entity", "list_entities", "seed",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def serve() -> Iterator[Any]:
    """Start an MCP HTTP server for a given core client; yield its /mcp URL."""
    servers: list[uvicorn.Server] = []

    def _start(core: Any, *, allow_anonymous: bool = False) -> str:
        port = _free_port()
        mcp = build_server(core, http=True, port=port)
        server = uvicorn.Server(uvicorn.Config(
            build_http_app(mcp, allow_anonymous=allow_anonymous),
            host="127.0.0.1", port=port, log_level="warning",
        ))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        servers.append(server)
        return f"http://127.0.0.1:{port}/mcp"

    yield _start
    for server in servers:
        server.should_exit = True


@asynccontextmanager
async def _session(url: str, token: str | None) -> AsyncIterator[ClientSession]:
    headers = {"authorization": f"Bearer {token}"} if token else {}
    async with (
        httpx.AsyncClient(headers=headers, timeout=30) as http,
        streamable_http_client(url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def _payload(result: Any) -> Any:
    return json.loads(result.content[0].text)


@pytest.fixture
def with_keys() -> Iterator[None]:
    original = settings.api_keys
    settings.api_keys = {
        "k_a": ApiKeyEntry(tenant_id="mcp_http_a", scope="write"),
        "k_b": ApiKeyEntry(tenant_id="mcp_http_b", scope="write"),
    }
    yield
    settings.api_keys = original


def test_http_rejects_requests_without_a_bearer(serve: Any, api: TestClient) -> None:
    url = serve(api)
    res = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


async def test_http_lists_tools_without_leaking_ctx(serve: Any, api: TestClient) -> None:
    url = serve(api)
    async with _session(url, "anything") as session:
        listed = (await session.list_tools()).tools
    assert {t.name for t in listed} == EXPECTED_TOOLS
    # `ctx` is injected by FastMCP, never a caller-supplied argument.
    assert all("ctx" not in t.inputSchema.get("properties", {}) for t in listed)


async def test_http_forwards_each_callers_credential_to_the_core(
    serve: Any, api: TestClient, with_keys: None
) -> None:
    url = serve(api)
    args = {"content": "pass-through works", "tenant_id": "mcp_http_a", "agent_id": "x"}
    async with _session(url, "k_a") as session:  # tenant_a's key → own tenant: allowed
        assert _payload(await session.call_tool("store", args))["status"] == "queued"
    async with _session(url, "k_b") as session:  # tenant_b's key → tenant_a: core says 403
        assert _payload(await session.call_tool("store", args)) == {"detail": "tenant mismatch"}
    async with _session(url, "not-a-key") as session:  # unknown key → core says 401
        assert "detail" in _payload(await session.call_tool("stats", {"tenant_id": "mcp_http_a"}))


async def test_http_never_lends_the_servers_own_key(
    serve: Any, api: TestClient, with_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even with a server-side key configured, an HTTP caller with a bad key gets nothing.
    monkeypatch.setenv("ARANGO_MEMORY_API_KEY", "k_a")
    url = serve(api)
    async with _session(url, "wrong") as session:
        out = _payload(await session.call_tool("stats", {"tenant_id": "mcp_http_a"}))
    assert "counts" not in out


class _SlowCore:
    """A core stub whose calls take 0.5 s — to show HTTP tool calls don't serialize."""

    def post(self, url: str, *, json: Any, headers: dict[str, str]) -> Any:
        time.sleep(0.5)
        return httpx.Response(200, json={"status": "ok"})

    def get(self, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> Any:
        return self.post(url, json=None, headers=headers)


async def test_http_tool_calls_run_concurrently(serve: Any) -> None:
    url = serve(_SlowCore())

    async def one() -> None:
        async with _session(url, "t") as session:
            await session.call_tool("flush", {"tenant_id": "t", "agent_id": "a"})

    started = time.perf_counter()
    async with anyio.create_task_group() as tg:
        for _ in range(4):
            tg.start_soon(one)
    # Serialized on the event loop this would take ≥ 2 s (4 × 0.5 s).
    assert time.perf_counter() - started < 1.5


def test_http_rejects_a_foreign_host_header(serve: Any, api: TestClient) -> None:
    url = serve(api)
    res = httpx.post(
        url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"authorization": "Bearer t", "host": "evil.example",
                 "accept": "application/json, text/event-stream"},
    )
    assert res.status_code == 421


async def test_anonymous_mode_serves_without_a_bearer(serve: Any, api: TestClient) -> None:
    url = serve(api, allow_anonymous=True)
    async with _session(url, None) as session:
        assert len((await session.list_tools()).tools) == len(EXPECTED_TOOLS)


def test_cli_refuses_unsafe_network_binds() -> None:
    with pytest.raises(SystemExit, match="allowed-hosts"):
        main(["--transport", "http", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit, match="localhost"):
        main(["--transport", "http", "--host", "0.0.0.0", "--allowed-hosts", "mcp.example",
              "--allow-anonymous"])
