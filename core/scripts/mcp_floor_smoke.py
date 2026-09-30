"""Smoke-test the MCP server's Streamable HTTP mode against the lowest supported `mcp` release.

Run via `make mcp-floor`, an isolated `uv run --with mcp==<floor>`: it builds the HTTP app, does
a real initialize + tools/list round-trip, and checks the bearer gate. 1.14.0 is the first
release where this passes (`transport_security` arrived in 1.10; parametrised `Context`
injection in 1.14), so the pyproject floor is `mcp>=1.14.0`.

Usage: python scripts/mcp_floor_smoke.py <path-to-core-src>
"""

from __future__ import annotations

import socket
import sys
import threading
import time

import httpx
import uvicorn


def main(src: str) -> None:
    sys.path.insert(0, src)
    from arango_memory.mcp.server import build_http_app, build_server

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    app = build_http_app(build_server(object(), http=True, port=port))  # type: ignore[arg-type]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    url = f"http://127.0.0.1:{port}/mcp"
    headers = {"authorization": "Bearer t", "accept": "application/json, text/event-stream"}
    init = {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "floor-smoke", "version": "0"},
    }
    res = httpx.post(url, headers=headers,
                     json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": init})
    assert res.status_code == 200, (res.status_code, res.text[:200])
    res = httpx.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert res.status_code == 200 and '"flush"' in res.text, res.text[:300]
    assert '"ctx"' not in res.text, "request context leaked into a tool schema"
    assert httpx.post(url, json={}).status_code == 401, "bearer gate missing"
    server.should_exit = True
    print("mcp floor smoke: OK")


if __name__ == "__main__":
    main(sys.argv[1])
