"""End-to-end check of the MCP server's OAuth mode (MCP-1) against a real IdP (Keycloak).

Uses the MCP SDK's own OAuth client, so the whole spec flow runs: 401 → protected-resource
metadata → authorization-server discovery → dynamic client registration → authorization code
+ PKCE (with the RFC 8707 resource indicator) → token → tool calls. Only the human step is
automated: the Keycloak login form is filled in headlessly.

Prereqs: a realm from scripts/keycloak_mcp_realm.py (with --dev and a test user), a core in
enforced mode with a delegate key, and the MCP server with --auth oauth. See docs/adapters/mcp.md.

Usage:
  KEYCLOAK_USER_PASSWORD=… python scripts/mcp_oauth_e2e.py --mcp-url http://127.0.0.1:18100/mcp \\
    --user alice --tenant <the user's tenant_id>
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

REDIRECT = "http://localhost:33418/callback"  # never served: the login redirect is intercepted


class MemoryStorage(TokenStorage):
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client = client_info


def _login(user: str, password: str) -> tuple[Any, Any]:
    """Redirect/callback handlers that complete a Keycloak login without a browser."""
    result: dict[str, str | None] = {}

    async def redirect_handler(auth_url: str) -> None:
        async with httpx.AsyncClient(follow_redirects=False, timeout=30) as http:
            page = await http.get(auth_url)
            form = re.search(r'<form[^>]*id="kc-form-login"[^>]*>', page.text)
            if form is None:
                raise RuntimeError(f"no Keycloak login form ({page.status_code})")
            action = html.unescape(re.search(r'action="([^"]+)"', form.group(0)).group(1))  # type: ignore[union-attr]
            # Keycloak marks its session cookies `Secure`; against a local http:// dev instance
            # httpx rightly won't send those, so carry them over explicitly (test harness only —
            # a real deployment's IdP is https).
            cookies = "; ".join(c.split(";", 1)[0] for c in page.headers.get_list("set-cookie"))
            done = await http.post(action, data={"username": user, "password": password},
                                   headers={"cookie": cookies})
            location = done.headers.get("location", "")
            query = parse_qs(urlparse(location).query)
            if "code" not in query:
                raise RuntimeError(f"login did not return a code ({done.status_code})")
            result["code"] = query["code"][0]
            result["state"] = query.get("state", [None])[0]

    async def callback_handler() -> tuple[str, str | None]:
        return str(result["code"]), result["state"]

    return redirect_handler, callback_handler


async def run(mcp_url: str, user: str, password: str, scope: str, tenant: str) -> dict[str, Any]:
    storage = MemoryStorage()
    redirect_handler, callback_handler = _login(user, password)
    auth = OAuthClientProvider(
        server_url=mcp_url,
        client_metadata=OAuthClientMetadata(
            client_name=f"mcp-oauth-e2e ({scope})",
            redirect_uris=[REDIRECT],  # type: ignore[list-item]
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=scope,
        ),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    out: dict[str, Any] = {"scope_requested": scope}
    async with (
        httpx.AsyncClient(auth=auth, timeout=60) as http,
        streamable_http_client(mcp_url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        out["tools"] = len((await session.list_tools()).tools)

        async def call(tool: str, **args: Any) -> Any:
            res = await session.call_tool(tool, args)
            return json.loads(res.content[0].text)

        ctx = {"tenant_id": tenant, "agent_id": "e2e"}
        out["store_own_tenant"] = await call("store", content="OAuth e2e memory", **ctx)
        out["store_other_tenant"] = await call("store", content="x", tenant_id="not-mine",
                                               agent_id="e2e")
        await call("flush", **ctx)
        hits = (await call("search", query="OAuth e2e memory", **ctx)).get("hits", [])
        out["search_hits"] = [h["text"] for h in hits]

    assert storage.tokens is not None and storage.client is not None
    claims = jwt.decode(storage.tokens.access_token, options={"verify_signature": False})
    out["registered_client_id"] = storage.client.client_id
    out["token"] = {k: claims.get(k)
                    for k in ("iss", "aud", "tenant_id", "scope", "memory_access", "azp")}
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mcp-url", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--tenant", required=True)
    p.add_argument("--scope", default="memory", help="OAuth scope the client requests")
    args = p.parse_args()
    password = os.environ["KEYCLOAK_USER_PASSWORD"]
    print(json.dumps(asyncio.run(run(args.mcp_url, args.user, password, args.scope,
                                     args.tenant)), indent=2))


if __name__ == "__main__":
    main()
