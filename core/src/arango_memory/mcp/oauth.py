"""MCP OAuth resource-server mode (MCP-1, DESIGN.md §21).

In this mode the MCP server is an OAuth 2.1 *resource server* per the MCP authorization spec:
it publishes protected-resource metadata (RFC 9728) pointing clients at an external
authorization server (the IdP, e.g. Keycloak), and accepts only access tokens the IdP issued
**for this MCP server** — the audience must be the MCP resource URL.

The spec forbids passing that token through to upstream APIs, so the server calls the core
with its own **delegate** credential instead, asserting the identity it verified (tenant,
scope, agents) in `X-On-Behalf-Of-*` headers; the core caps what a delegate may assert.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import anyio
import jwt
from mcp.server.auth.provider import AccessToken

from ..security.jwt_auth import claim_tokens, scope_from_claim

logger = logging.getLogger(__name__)


class VerifiedToken(AccessToken):
    """An access token plus the claims it was verified with. A subclass field (the SDK allows
    them) rather than `AccessToken.claims`, which only exists from mcp 1.28 — older releases
    within our `mcp>=1.14` floor would silently drop it."""

    verified_claims: dict[str, Any]


@dataclass(frozen=True)
class OAuthConfig:
    """Where tokens come from and how their claims map to a core identity."""

    issuer: str  # the authorization server (IdP) issuer URL
    resource_url: str  # this MCP server's public URL, e.g. https://mcp.example.com/mcp
    delegate_key: str  # the core API key marked `delegate`
    audience: str | None = None  # defaults to resource_url (MCP spec: token bound to us)
    jwks_uri: str | None = None  # defaults to the IdP's OIDC discovery `jwks_uri`
    algorithms: tuple[str, ...] = ("RS256",)
    tenant_claim: str = "tenant_id"
    # Claim mapped to the core's read|write|consolidate (any value containing "write" → write).
    # The OAuth `scope` claim by default; point it at a role claim (e.g. Keycloak realm roles)
    # to make write a user entitlement rather than something a client can request.
    scope_claim: str = "scope"
    agent_claim: str | None = None
    required_scopes: tuple[str, ...] = field(default_factory=tuple)
    leeway_seconds: int = 60

    @property
    def expected_audience(self) -> str:
        return self.audience or self.resource_url


def delegation_headers(config: OAuthConfig, claims: dict[str, Any]) -> dict[str, str]:
    """Core request headers for a verified caller: the delegate key + the asserted identity.

    Scope maps exactly as the core maps JWT scopes (read|write|consolidate); the core then
    caps it by the delegate key's own scope.
    """
    headers = {
        "authorization": f"Bearer {config.delegate_key}",
        "x-on-behalf-of-tenant": str(claims[config.tenant_claim]),
        "x-on-behalf-of-scope": scope_from_claim(claims.get(config.scope_claim)),
    }
    if config.agent_claim is not None:
        agents = claim_tokens(claims.get(config.agent_claim))
        if agents:
            headers["x-on-behalf-of-agents"] = ",".join(agents)
    return headers


class JwtTokenVerifier:
    """Verifies IdP-issued JWT access tokens for this MCP server (an SDK `TokenVerifier`).

    Signature against the IdP's JWKS (keys cached + rotated by `kid`), algorithm allow-listed,
    `iss`/`aud`/`exp` required. Fail-closed: any verification or JWKS error rejects the token.
    """

    def __init__(self, config: OAuthConfig) -> None:
        self.config = config
        self._jwks: jwt.PyJWKClient | None = None

    def _jwks_client(self) -> jwt.PyJWKClient:
        if self._jwks is None:
            uri = self.config.jwks_uri
            if uri is None:
                discovery = f"{self.config.issuer.rstrip('/')}/.well-known/openid-configuration"
                with urllib.request.urlopen(discovery, timeout=10) as res:  # noqa: S310 — https IdP
                    uri = str(json.load(res)["jwks_uri"])
            self._jwks = jwt.PyJWKClient(uri)
        return self._jwks

    def _decode(self, token: str) -> dict[str, Any]:
        key = self._jwks_client().get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            key.key,
            algorithms=list(self.config.algorithms),
            audience=self.config.expected_audience,
            issuer=self.config.issuer,
            leeway=self.config.leeway_seconds,
            options={"require": ["exp", "iss", "aud"]},
        )
        return claims

    async def verify_token(self, token: str) -> VerifiedToken | None:
        try:
            claims = await anyio.to_thread.run_sync(self._decode, token)
        except Exception as exc:  # noqa: BLE001 — fail closed: any verification/JWKS error rejects
            # Log why (never the token itself) — a rejected token is otherwise an opaque 401.
            logger.warning("mcp oauth token rejected: %s: %s", type(exc).__name__, exc)
            return None
        if not claims.get(self.config.tenant_claim):
            logger.warning("mcp oauth token rejected: missing tenant claim %r",
                           self.config.tenant_claim)
            return None
        return VerifiedToken(
            token=token,
            client_id=str(claims.get("azp") or claims.get("client_id") or ""),
            # OAuth scopes (what `required_scopes` gates on) always come from the standard
            # `scope` claim; `scope_claim` only drives the core read/write mapping.
            scopes=claim_tokens(claims.get("scope")),
            expires_at=claims.get("exp"),
            resource=self.config.expected_audience,
            verified_claims=claims,
        )
