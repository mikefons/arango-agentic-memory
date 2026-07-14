"""OIDC / JWT bearer verification (DESIGN.md §17).

Verifies an RS256 (config-allowlisted) bearer JWT against the issuer's JWKS and
maps its claims to a `Principal` — the same identity object static API keys yield,
so the rest of the auth/authz path is unchanged. Enabled by setting `oidc_issuer`;
coexists with static `api_keys`.

Security posture: signature is verified against the JWKS (keys cached + rotated by
`kid`), the algorithm is constrained to `settings.oidc_algorithms` (so `alg: none`
and HS/RS confusion are rejected), and `exp`/`nbf`/`iss`/`aud` are validated with a
small clock-skew leeway. Revocation is by expiry only (use short-lived tokens).
"""

from __future__ import annotations

import jwt
from fastapi import HTTPException

from ..config import settings
from .auth import Principal

_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}

# One JWKS client per JWKS URI, reused across requests (it caches signing keys by
# kid and refreshes on rotation). Built lazily so import stays side-effect-free.
_jwks_clients: dict[str, jwt.PyJWKClient] = {}


def _jwks_uri() -> str:
    if settings.oidc_jwks_uri:
        return settings.oidc_jwks_uri
    if not settings.oidc_issuer:  # pragma: no cover — guarded by the caller
        raise RuntimeError("oidc_issuer is not configured")
    return f"{settings.oidc_issuer.rstrip('/')}/.well-known/jwks.json"


def _jwks_client() -> jwt.PyJWKClient:
    uri = _jwks_uri()
    client = _jwks_clients.get(uri)
    if client is None:
        client = jwt.PyJWKClient(uri)
        _jwks_clients[uri] = client
    return client


def _claim_tokens(value: object) -> list[str]:
    """Normalize a claim value to tokens: a space-delimited string (OAuth2 `scope`) or
    a list (roles)."""
    if isinstance(value, str):
        return value.split()
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def _scope_from_claim(value: object) -> str:
    """Map the scope/roles claim to read|write|consolidate (highest wins; MA-7)."""
    tokens = [t.lower() for t in _claim_tokens(value)]
    if any("consolidate" in t for t in tokens):
        return "consolidate"
    return "write" if any("write" in t for t in tokens) else "read"


def _agents_from_claim(claims: dict[str, object]) -> tuple[str, ...] | None:
    """Map the configured agent claim (MA-7) to an allow-list, or None (any agent)."""
    if settings.oidc_agent_claim is None:
        return None
    tokens = _claim_tokens(claims.get(settings.oidc_agent_claim))
    return tuple(tokens) if tokens else None


def verify_jwt(token: str) -> Principal:
    """Verify a bearer JWT and map its claims to a `Principal`. Raises 401 on any
    verification failure or a missing tenant claim. Fail-closed: a JWKS fetch
    error (issuer unreachable) is a 401, never an open pass."""
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(settings.oidc_algorithms),
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            leeway=settings.oidc_leeway_seconds,
            options={
                "require": ["exp", "iss"],
                "verify_aud": settings.oidc_audience is not None,
            },
        )
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
        raise HTTPException(
            status_code=401, detail=f"invalid bearer token: {exc}", headers=_UNAUTHORIZED
        ) from exc

    tenant = claims.get(settings.oidc_tenant_claim)
    if not tenant:
        raise HTTPException(
            status_code=401,
            detail=f"token missing tenant claim '{settings.oidc_tenant_claim}'",
            headers=_UNAUTHORIZED,
        )
    scope = _scope_from_claim(claims.get(settings.oidc_scope_claim))
    return Principal(
        tenant_id=str(tenant), scope=scope, agent_ids=_agents_from_claim(claims)
    )
