"""FastMCP server exposing the core as MCP tools (DESIGN.md §21).

A thin wrapper over the core's /v1 HTTP API for Claude Desktop / Claude Code / Cursor /
Windsurf. Two transports:

- **stdio** (default) — `python -m arango_memory.mcp`. The local client owns the process,
  which calls the core with `ARANGO_MEMORY_API_KEY` (if set).
- **Streamable HTTP** — `python -m arango_memory.mcp --transport http`. A standalone network
  service at `/mcp`. **Credentials pass through:** every request must carry the caller's own
  `Authorization: Bearer …` (401 otherwise), and that header is forwarded to the core on each
  tool call, so the core's ABAC (tenant-bound keys / JWTs, per-agent binding, scopes) applies
  per caller. The server's own `ARANGO_MEMORY_API_KEY` is never used for HTTP callers.
  Stateless (no session affinity), so it runs behind a load balancer.

The core URL comes from `ARANGO_MEMORY_CORE_URL` (default http://localhost:8080). Exposes the
full §19 surface as 11 tools (store/search/prime/flush/record_step/list_steps/forget/stats/
get_entity/list_entities/seed).
"""

from __future__ import annotations

import argparse
import functools
import os
from collections.abc import Callable, Sequence
from typing import Any, cast

import anyio
import httpx
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.types import ASGIApp, Receive, Scope, Send

from . import tools
from .oauth import JwtTokenVerifier, OAuthConfig, VerifiedToken, delegation_headers
from .tools import CoreClient

_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

# FastMCP injects the request context into a tool parameter annotated with this type.
Ctx = Context[Any, Any, Any]


class _WithHeaders:
    """A `CoreClient` that adds per-caller headers to every core request: the caller's own
    `Authorization` (bearer mode) or the delegate key + asserted identity (OAuth mode)."""

    def __init__(self, inner: Any, headers: dict[str, str]) -> None:
        self._inner = inner
        self._headers = headers

    def post(self, url: str, *, json: Any) -> Any:
        return self._inner.post(url, json=json, headers=self._headers)

    def get(self, url: str, *, params: dict[str, Any]) -> Any:
        return self._inner.get(url, params=params, headers=self._headers)


def build_server(
    client: CoreClient | None = None,
    *,
    http: bool = False,
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp",
    allowed_hosts: Sequence[str] = (),
    allowed_origins: Sequence[str] = (),
    oauth: OAuthConfig | None = None,
) -> FastMCP:
    """Build the MCP server. Tests inject a client; production uses httpx over HTTP.

    `http=True` configures Streamable HTTP (stateless) and switches tool calls to forwarding
    each caller's own credential instead of the server's env key. `oauth` (HTTP only) makes it
    an OAuth resource server (MCP-1): IdP-issued tokens for this server, delegated to the core.
    """
    if oauth is not None and not http:
        raise ValueError("OAuth mode requires the HTTP transport")
    base_url = os.environ.get("ARANGO_MEMORY_CORE_URL", "http://localhost:8080")
    # stdio: the server's own bearer key when the core enforces auth (§17). HTTP: never —
    # callers bring their own, so an anonymous network caller can't borrow the server's.
    api_key = None if http else os.environ.get("ARANGO_MEMORY_API_KEY")
    headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
    base = client or cast(
        CoreClient, httpx.Client(base_url=base_url, timeout=30.0, headers=headers)
    )

    security = None
    if allowed_hosts or allowed_origins:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(allowed_hosts),
            allowed_origins=list(allowed_origins),
        )
    auth: dict[str, Any] = {}
    if oauth is not None:
        auth = {
            "auth": AuthSettings(
                issuer_url=AnyHttpUrl(oauth.issuer),
                resource_server_url=AnyHttpUrl(oauth.resource_url),
                required_scopes=list(oauth.required_scopes) or None,
            ),
            "token_verifier": JwtTokenVerifier(oauth),
        }
    server = FastMCP(
        "arango-memory",
        host=host,
        port=port,
        streamable_http_path=path,
        stateless_http=True,
        transport_security=security,  # None on localhost → the SDK's own localhost guard
        **auth,
    )

    def core_for(ctx: Ctx) -> CoreClient:
        if not http:
            return base
        if oauth is not None:
            # The SDK's auth middleware verified the token before any tool runs; never forward
            # it (MCP spec: no token passthrough) — delegate with our own key instead.
            token = get_access_token()
            if not isinstance(token, VerifiedToken):
                raise PermissionError("no verified access token for this request")
            return cast(CoreClient,
                        _WithHeaders(base, delegation_headers(oauth, token.verified_claims)))
        request = ctx.request_context.request
        auth_header = request.headers.get("authorization") if request is not None else None
        headers = {"authorization": auth_header} if auth_header else {}
        return cast(CoreClient, _WithHeaders(base, headers))

    async def call(fn: Callable[..., Any], ctx: Ctx, **kwargs: Any) -> Any:
        # Tool logic is sync httpx; run it off the event loop so one slow call (e.g. `flush`)
        # doesn't stall every other HTTP client.
        return await anyio.to_thread.run_sync(functools.partial(fn, core_for(ctx), **kwargs))

    @server.tool()
    async def store(content: str, tenant_id: str, agent_id: str, ctx: Ctx) -> Any:
        """Store one turn of memory for a tenant/agent."""
        return await call(
            tools.store_memory, ctx, content=content, tenant_id=tenant_id, agent_id=agent_id
        )

    @server.tool()
    async def search(
        query: str, tenant_id: str, agent_id: str, ctx: Ctx,
        mode: str = "lite", read_agent_ids: list[str] | None = None,
    ) -> Any:
        """Retrieve relevant memories for a query (assembled context + hits).

        read_agent_ids reads across several agents in one fused pass (MA-2)."""
        return await call(
            tools.search_memory, ctx, query=query, tenant_id=tenant_id, agent_id=agent_id,
            mode=mode, read_agent_ids=read_agent_ids,
        )

    @server.tool()
    async def prime(
        task: str, tenant_id: str, agent_id: str, ctx: Ctx,
        mode: str = "lite", read_agent_ids: list[str] | None = None,
    ) -> Any:
        """Brief for a task before picking up a job (MA-3): retrieved history + key
        entities + prior tool runs, spanning read_agent_ids. The handoff entry point."""
        return await call(
            tools.prime_memory, ctx, task=task, tenant_id=tenant_id, agent_id=agent_id,
            mode=mode, read_agent_ids=read_agent_ids,
        )

    @server.tool()
    async def flush(
        tenant_id: str, agent_id: str, ctx: Ctx, timeout_ms: int = 5000
    ) -> Any:
        """Block until this tenant's queued writes are committed + retrievable (MA-1) —
        call between agent stages so the next agent reads the previous one's writes."""
        return await call(
            tools.flush_memory, ctx, tenant_id=tenant_id, agent_id=agent_id,
            timeout_ms=timeout_ms,
        )

    @server.tool()
    async def record_step(
        tool_name: str, arguments: dict[str, Any], outcome: str, tenant_id: str,
        agent_id: str, ctx: Ctx,
    ) -> Any:
        """Record a completed tool call as procedural memory."""
        return await call(
            tools.record_step, ctx, tool_name=tool_name, arguments=arguments,
            outcome=outcome, tenant_id=tenant_id, agent_id=agent_id,
        )

    @server.tool()
    async def list_steps(
        tenant_id: str, agent_id: str, ctx: Ctx, tool_name: str | None = None
    ) -> Any:
        """List recorded procedural memories (tool traces)."""
        return await call(
            tools.list_steps, ctx, tenant_id=tenant_id, agent_id=agent_id, tool_name=tool_name
        )

    @server.tool()
    async def forget(tenant_id: str, ctx: Ctx, agent_id: str | None = None) -> Any:
        """Right to be forgotten: soft-delete a tenant's (or one agent's) memories."""
        return await call(tools.forget_memory, ctx, tenant_id=tenant_id, agent_id=agent_id)

    @server.tool()
    async def stats(tenant_id: str, ctx: Ctx) -> Any:
        """Per-tenant graph health counts."""
        return await call(tools.graph_stats, ctx, tenant_id=tenant_id)

    @server.tool()
    async def get_entity(entity_id: str, tenant_id: str, ctx: Ctx) -> Any:
        """Fetch a semantic entity (by id) plus its related entities."""
        return await call(tools.get_entity, ctx, entity_id=entity_id, tenant_id=tenant_id)

    @server.tool()
    async def list_entities(
        tenant_id: str, ctx: Ctx,
        agent_id: str | None = None, label: str | None = None,
    ) -> Any:
        """List a tenant's semantic entities (optionally filtered by agent/label)."""
        return await call(
            tools.list_entities, ctx, tenant_id=tenant_id, agent_id=agent_id, label=label
        )

    @server.tool()
    async def seed(
        profile: dict[str, Any], tenant_id: str, agent_id: str, ctx: Ctx
    ) -> Any:
        """Cold-start seed: pre-populate semantic memory from a profile."""
        return await call(
            tools.seed_profile, ctx, profile=profile, tenant_id=tenant_id, agent_id=agent_id
        )

    return server


class _RequireBearer:
    """Pure-ASGI gate: 401 any HTTP request without a Bearer credential. (Starlette's
    BaseHTTPMiddleware would buffer the Streamable HTTP event stream, so it isn't used.)"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"")
            if not auth.lower().startswith(b"bearer ") or not auth[7:].strip():
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"error":"missing bearer credential"}',
                })
                return
        await self.app(scope, receive, send)


class _Health:
    """`GET /health` liveness for orchestrators: answered before auth, without touching the
    core (like the core's own /health — up means the process serves, not that the core does)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == "/health":
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": b'{"status":"ok"}'})
            return
        await self.app(scope, receive, send)


def build_http_app(
    server: FastMCP, *, allow_anonymous: bool = False, oauth: bool = False
) -> ASGIApp:
    """The Streamable HTTP ASGI app with an unauthenticated `/health`. Bearer mode gates it with
    `_RequireBearer` (unless `allow_anonymous`); OAuth mode relies on the SDK's own auth, which
    401s with the RFC 9728 metadata pointer and leaves the metadata route public."""
    app: ASGIApp = server.streamable_http_app()
    if oauth or allow_anonymous:
        return _Health(app)
    return _Health(_RequireBearer(app))


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    env = os.environ.get
    p = argparse.ArgumentParser(prog="python -m arango_memory.mcp")
    p.add_argument("--transport", choices=("stdio", "http"),
                   default=env("ARANGO_MEMORY_MCP_TRANSPORT", "stdio"))
    p.add_argument("--host", default=env("ARANGO_MEMORY_MCP_HOST", "127.0.0.1"))
    # `PORT` is the platform convention (Railway, Cloud Run, Heroku) for the port to listen on.
    p.add_argument("--port", type=int,
                   default=int(env("ARANGO_MEMORY_MCP_PORT") or env("PORT") or "8000"))
    p.add_argument("--path", default=env("ARANGO_MEMORY_MCP_PATH", "/mcp"))
    p.add_argument("--allowed-hosts", default=env("ARANGO_MEMORY_MCP_ALLOWED_HOSTS"),
                   help="comma-separated Host values accepted (required off localhost)")
    p.add_argument("--allowed-origins", default=env("ARANGO_MEMORY_MCP_ALLOWED_ORIGINS"),
                   help="comma-separated Origin values accepted from browsers")
    p.add_argument("--allow-anonymous", action="store_true",
                   default=env("ARANGO_MEMORY_MCP_ALLOW_ANONYMOUS", "") == "1",
                   help="accept requests without a bearer credential (localhost only)")
    p.add_argument("--auth", choices=("bearer", "oauth"),
                   default=env("ARANGO_MEMORY_MCP_AUTH", "bearer"),
                   help="bearer: forward the caller's core credential; oauth: MCP OAuth "
                        "resource server delegating to the core (needs ARANGO_MEMORY_DELEGATE_KEY)")
    p.add_argument("--oauth-issuer", default=env("ARANGO_MEMORY_MCP_OAUTH_ISSUER"))
    p.add_argument("--resource-url", default=env("ARANGO_MEMORY_MCP_RESOURCE_URL"),
                   help="this server's public MCP URL (the token audience)")
    p.add_argument("--oauth-audience", default=env("ARANGO_MEMORY_MCP_OAUTH_AUDIENCE"))
    p.add_argument("--oauth-jwks-uri", default=env("ARANGO_MEMORY_MCP_OAUTH_JWKS_URI"))
    p.add_argument("--oauth-tenant-claim",
                   default=env("ARANGO_MEMORY_MCP_OAUTH_TENANT_CLAIM", "tenant_id"))
    p.add_argument("--oauth-scope-claim",
                   default=env("ARANGO_MEMORY_MCP_OAUTH_SCOPE_CLAIM", "scope"))
    p.add_argument("--oauth-agent-claim", default=env("ARANGO_MEMORY_MCP_OAUTH_AGENT_CLAIM"))
    p.add_argument("--required-scopes", default=env("ARANGO_MEMORY_MCP_REQUIRED_SCOPES"),
                   help="comma-separated scopes every token must carry")
    return p.parse_args(argv)


def _oauth_config(args: argparse.Namespace) -> OAuthConfig:
    # The delegate key is read from the environment only — never a flag, so it can't leak
    # into process listings or shell history.
    delegate_key = os.environ.get("ARANGO_MEMORY_DELEGATE_KEY")
    missing = [name for name, value in (
        ("--oauth-issuer", args.oauth_issuer),
        ("--resource-url", args.resource_url),
        ("ARANGO_MEMORY_DELEGATE_KEY", delegate_key),
    ) if not value]
    if missing:
        raise SystemExit(f"--auth oauth needs: {', '.join(missing)}")
    assert delegate_key is not None
    return OAuthConfig(
        issuer=args.oauth_issuer,
        resource_url=args.resource_url,
        delegate_key=delegate_key,
        audience=args.oauth_audience,
        jwks_uri=args.oauth_jwks_uri,
        tenant_claim=args.oauth_tenant_claim,
        scope_claim=args.oauth_scope_claim,
        agent_claim=args.oauth_agent_claim,
        required_scopes=tuple(_csv(args.required_scopes)),
    )


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.transport == "stdio":
        build_server().run()
        return

    local = args.host in _LOCAL_HOSTS
    hosts, origins = _csv(args.allowed_hosts), _csv(args.allowed_origins)
    if not local and not hosts:
        raise SystemExit(
            f"refusing to serve MCP on {args.host} without --allowed-hosts "
            "(DNS-rebinding protection needs the Host values clients will use)"
        )
    if args.allow_anonymous and not local:
        raise SystemExit("--allow-anonymous is only permitted on a localhost bind")
    oauth = None
    if args.auth == "oauth":
        if args.allow_anonymous:
            raise SystemExit("--allow-anonymous cannot be combined with --auth oauth")
        oauth = _oauth_config(args)

    import uvicorn

    server = build_server(
        http=True, host=args.host, port=args.port, path=args.path,
        allowed_hosts=hosts, allowed_origins=origins, oauth=oauth,
    )
    uvicorn.run(
        build_http_app(server, allow_anonymous=args.allow_anonymous, oauth=oauth is not None),
        host=args.host, port=args.port,
    )


if __name__ == "__main__":
    main()
