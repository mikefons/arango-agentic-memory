"""MCP OAuth resource-server mode (MCP-1): discovery, audience-bound tokens, delegation.

A local test IdP (OIDC discovery + JWKS over HTTP) issues RS256 tokens. The MCP server must
publish RFC 9728 metadata, accept only tokens issued for *it*, and call the core with its own
delegate key — never the caller's token (MCP spec: no token passthrough).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from arango_memory.config import ApiKeyEntry, settings
from arango_memory.mcp.oauth import OAuthConfig
from arango_memory.mcp.server import build_http_app, build_server, main

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_ROGUE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "test-key"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def issuer() -> Iterator[str]:
    """A minimal OIDC issuer: discovery document + JWKS."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key()))
    jwk.update({"kid": _KID, "use": "sig", "alg": "RS256"})
    docs = {
        "/.well-known/openid-configuration": {"issuer": base, "jwks_uri": f"{base}/jwks"},
        "/jwks": {"keys": [jwk]},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server API
            body = json.dumps(docs.get(self.path, {})).encode()
            self.send_response(200 if self.path in docs else 404)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield base
    httpd.shutdown()


def _token(issuer: str, aud: str, *, key: rsa.RSAPrivateKey = _KEY, **claims: Any) -> str:
    now = int(time.time())
    body = {"iss": issuer, "aud": aud, "sub": "user-1", "exp": now + 300, "iat": now,
            "tenant_id": "oauth_t", "scope": "memory write", **claims}
    return jwt.encode(body, key, algorithm="RS256", headers={"kid": _KID})


class _RecordingCore:
    """Records the headers the MCP server sends to the core."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []

    def post(self, url: str, *, json: Any, headers: dict[str, str]) -> Any:
        self.headers.append(headers)
        return httpx.Response(200, json={"status": "queued"})

    def get(self, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> Any:
        return self.post(url, json=None, headers=headers)


@pytest.fixture
def serve_oauth(issuer: str) -> Iterator[Any]:
    servers: list[uvicorn.Server] = []

    def _start(core: Any, *, required_scopes: tuple[str, ...] = (),
               scope_claim: str = "scope") -> tuple[str, OAuthConfig]:
        port = _free_port()
        url = f"http://127.0.0.1:{port}/mcp"
        config = OAuthConfig(issuer=issuer, resource_url=url, delegate_key="d_mcp",
                             required_scopes=required_scopes, scope_claim=scope_claim)
        app = build_http_app(build_server(core, http=True, port=port, oauth=config), oauth=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        while not server.started:
            time.sleep(0.05)
        servers.append(server)
        return url, config

    yield _start
    for server in servers:
        server.should_exit = True


@asynccontextmanager
async def _session(url: str, token: str) -> AsyncIterator[ClientSession]:
    async with (
        httpx.AsyncClient(headers={"authorization": f"Bearer {token}"}, timeout=30) as http,
        streamable_http_client(url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def _rpc(url: str, token: str | None) -> httpx.Response:
    headers = {"accept": "application/json, text/event-stream"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    return httpx.post(url, headers=headers,
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


def test_publishes_protected_resource_metadata(serve_oauth: Any, issuer: str) -> None:
    url, _ = serve_oauth(_RecordingCore(), required_scopes=("memory",))
    origin = url.removesuffix("/mcp")
    meta = httpx.get(f"{origin}/.well-known/oauth-protected-resource/mcp").json()
    assert meta["resource"] == url
    assert [s.rstrip("/") for s in meta["authorization_servers"]] == [issuer]
    assert meta["scopes_supported"] == ["memory"]


def test_unauthenticated_request_points_at_the_metadata(serve_oauth: Any) -> None:
    url, _ = serve_oauth(_RecordingCore())
    res = _rpc(url, None)
    assert res.status_code == 401
    assert "resource_metadata=" in res.headers["www-authenticate"]
    assert "/.well-known/oauth-protected-resource/mcp" in res.headers["www-authenticate"]


@pytest.mark.parametrize("bad", ["wrong_audience", "rogue_signature", "wrong_issuer",
                                 "expired", "no_tenant"])
def test_rejects_tokens_not_issued_for_this_server(
    serve_oauth: Any, issuer: str, bad: str
) -> None:
    url, _ = serve_oauth(_RecordingCore())
    token = {
        # e.g. a token minted for the *core* — accepting it would be token passthrough
        "wrong_audience": lambda: _token(issuer, "https://core.example"),
        "rogue_signature": lambda: _token(issuer, url, key=_ROGUE),
        "wrong_issuer": lambda: _token("https://evil.example", url),
        "expired": lambda: _token(issuer, url, exp=int(time.time()) - 3600),
        "no_tenant": lambda: _token(issuer, url, tenant_id=None),
    }[bad]()
    assert _rpc(url, token).status_code == 401


def test_missing_required_scope_is_403(serve_oauth: Any, issuer: str) -> None:
    url, _ = serve_oauth(_RecordingCore(), required_scopes=("memory",))
    assert _rpc(url, _token(issuer, url, scope="write")).status_code == 403


async def test_calls_the_core_with_the_delegate_key_never_the_callers_token(
    serve_oauth: Any, issuer: str
) -> None:
    core = _RecordingCore()
    url, _ = serve_oauth(core)
    token = _token(issuer, url, scope="memory write")
    async with _session(url, token) as session:
        await session.call_tool("store", {"content": "x", "tenant_id": "oauth_t",
                                          "agent_id": "a"})
    (sent,) = core.headers
    assert sent["authorization"] == "Bearer d_mcp"
    assert token not in json.dumps(sent)
    assert sent["x-on-behalf-of-tenant"] == "oauth_t"
    assert sent["x-on-behalf-of-scope"] == "write"


@pytest.fixture
def with_delegate_key() -> Iterator[None]:
    original = settings.api_keys
    settings.api_keys = {"d_mcp": ApiKeyEntry(tenant_id="*", scope="write", delegate=True)}
    yield
    settings.api_keys = original


async def test_end_to_end_identity_comes_from_the_token(
    serve_oauth: Any, issuer: str, api: TestClient, with_delegate_key: None
) -> None:
    url, _ = serve_oauth(api)

    async def store(token: str, tenant: str) -> Any:
        async with _session(url, token) as session:
            out = await session.call_tool("store", {"content": "via oauth",
                                                    "tenant_id": tenant, "agent_id": "a"})
        return json.loads(out.content[0].text)

    writer = _token(issuer, url, scope="memory write")
    assert (await store(writer, "oauth_t"))["status"] == "queued"
    # The token says tenant oauth_t; naming another tenant in the tool args doesn't help.
    assert await store(writer, "someone_else") == {"detail": "tenant mismatch"}
    # A read-scoped token can't write, whatever the tool call asks for.
    reader = _token(issuer, url, scope="memory")
    assert (await store(reader, "oauth_t")) == {"detail": "write access required"}


def test_cli_oauth_requires_its_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARANGO_MEMORY_DELEGATE_KEY", raising=False)
    with pytest.raises(SystemExit, match="--oauth-issuer.*--resource-url.*DELEGATE_KEY"):
        main(["--transport", "http", "--auth", "oauth"])
    monkeypatch.setenv("ARANGO_MEMORY_DELEGATE_KEY", "d")
    with pytest.raises(SystemExit, match="cannot be combined"):
        main(["--transport", "http", "--auth", "oauth", "--allow-anonymous",
              "--oauth-issuer", "https://idp", "--resource-url", "https://mcp/mcp"])
    with pytest.raises(ValueError, match="HTTP transport"):
        build_server(object(), oauth=OAuthConfig(issuer="https://idp",  # type: ignore[arg-type]
                                                 resource_url="https://m/mcp",
                                                 delegate_key="d"))


async def test_role_claim_drives_core_scope_while_oauth_scope_gates_access(
    serve_oauth: Any, issuer: str
) -> None:
    # Keycloak-style setup: write is a user role (`memory_access`), while the OAuth `scope`
    # claim still gates access via required_scopes. Regression: the required-scope check once
    # read the role claim instead of `scope`.
    core = _RecordingCore()
    url, _ = serve_oauth(core, required_scopes=("memory",), scope_claim="memory_access")
    writer = _token(issuer, url, scope="memory", memory_access=["memory-writer"])
    async with _session(url, writer) as session:
        await session.call_tool("stats", {"tenant_id": "oauth_t"})
    assert core.headers[-1]["x-on-behalf-of-scope"] == "write"

    reader = _token(issuer, url, scope="memory")  # no role → read
    async with _session(url, reader) as session:
        await session.call_tool("stats", {"tenant_id": "oauth_t"})
    assert core.headers[-1]["x-on-behalf-of-scope"] == "read"

    # A role without the OAuth scope is still refused at the door.
    no_scope = _token(issuer, url, scope="profile", memory_access=["memory-writer"])
    assert _rpc(url, no_scope).status_code == 403
