"""Smoke-test the MCP server's Streamable HTTP mode against the lowest supported `mcp` release.

Run via `make mcp-floor`, an isolated `uv run --with mcp==<floor>`. It builds the HTTP app, does
a real initialize + tools/list round-trip and checks the bearer gate, then repeats in OAuth mode
(MCP-1) against a local test issuer: protected-resource metadata, an audience-bound token, and a
tool call that must reach the core with the delegate key.

1.17.0 is the floor (`mcp>=1.17.0`): `transport_security` arrived in 1.10, parametrised
`Context` injection in 1.14, and RFC 9728 path-inserted protected-resource metadata
(`/.well-known/oauth-protected-resource/mcp`), which OAuth mode needs, in 1.17.

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
    _oauth_smoke()
    print("mcp floor smoke: OK (bearer + oauth)")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _oauth_smoke() -> None:
    """OAuth mode (MCP-1): metadata, audience-bound token, delegation to the core."""
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from arango_memory.mcp.oauth import OAuthConfig
    from arango_memory.mcp.server import build_http_app, build_server

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    idp_port = _free_port()
    issuer = f"http://127.0.0.1:{idp_port}"
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "k", "use": "sig", "alg": "RS256"})
    docs = {"/.well-known/openid-configuration": {"issuer": issuer, "jwks_uri": f"{issuer}/jwks"},
            "/jwks": {"keys": [jwk]}}

    class Idp(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(docs[self.path]).encode())

        def log_message(self, *args: object) -> None:
            pass

    threading.Thread(target=HTTPServer(("127.0.0.1", idp_port), Idp).serve_forever,
                     daemon=True).start()

    sent: list[dict[str, str]] = []

    class Core:
        def post(self, url: str, *, json: object, headers: dict[str, str]) -> httpx.Response:
            sent.append(headers)
            return httpx.Response(200, json={"counts": {}})

        def get(self, url: str, *, params: object, headers: dict[str, str]) -> httpx.Response:
            return self.post(url, json=None, headers=headers)

    port = _free_port()
    resource = f"http://127.0.0.1:{port}/mcp"
    config = OAuthConfig(issuer=issuer, resource_url=resource, delegate_key="d")
    app = build_http_app(build_server(Core(), http=True, port=port, oauth=config),  # type: ignore[arg-type]
                         oauth=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    meta = httpx.get(f"http://127.0.0.1:{port}/.well-known/oauth-protected-resource/mcp").json()
    assert meta["resource"] == resource, meta
    now = int(time.time())
    token = jwt.encode({"iss": issuer, "aud": resource, "exp": now + 60, "tenant_id": "t",
                        "scope": "write"}, key, algorithm="RS256", headers={"kid": "k"})
    headers = {"authorization": f"Bearer {token}", "accept": "application/json, text/event-stream"}
    init = {"protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "floor-smoke", "version": "0"}}
    assert httpx.post(resource, headers=headers, json={"jsonrpc": "2.0", "id": 1,
                      "method": "initialize", "params": init}).status_code == 200
    res = httpx.post(resource, headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "stats", "arguments": {"tenant_id": "t"}}})
    assert res.status_code == 200, res.text[:300]
    assert sent and sent[-1]["authorization"] == "Bearer d", "core not called with delegate key"
    assert sent[-1]["x-on-behalf-of-tenant"] == "t" and sent[-1]["x-on-behalf-of-scope"] == "write"
    assert httpx.post(resource, json={}).status_code == 401
    server.should_exit = True


if __name__ == "__main__":
    main(sys.argv[1])
