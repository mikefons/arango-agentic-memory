"""API authentication (DESIGN.md §17): bearer credentials → a `Principal`.

Keyless by default — when neither `api_keys` nor `oidc_issuer` is configured the
core runs **open** (the caller's body-asserted `tenant_id`/`access_level` are
trusted, the dev/CI/demo posture). When either is configured, every `/v1` route
requires `Authorization: Bearer <token>`:
  - an **OIDC/JWT** (when `oidc_issuer` is set) — verified against the issuer's
    JWKS and mapped to a `Principal` (see `jwt_auth.verify_jwt`);
  - a **static API key** — looked up in `api_keys`.
Both yield a `Principal` stashed on `request.state` for handlers to authorize
against (authz lives in the route layer, AUTH-2). A missing/invalid credential is
a `401`. `/health` + the OpenAPI docs are always exempt.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from ..config import settings

_OPEN_PATHS = frozenset({"/health", "/ready", "/docs", "/openapi.json", "/redoc"})


@dataclass(frozen=True)
class Principal:
    """An authenticated caller: the tenant it owns and what it may do."""

    tenant_id: str
    scope: str  # "read" | "write" | "consolidate" (ordered; MA-7)
    # Agents this caller may act as (MA-7). None → any agent. Entries may be a glob-lite
    # suffix (`"research::*"`). Restricts writes (403) and filters cross-agent reads.
    agent_ids: tuple[str, ...] | None = None


def agent_allowed(allowed: tuple[str, ...] | None, agent_id: str) -> bool:
    """True if `agent_id` is permitted by an `agent_ids` allow-list (MA-7).

    `None` allows any agent. A pattern ending in `*` matches by prefix
    (`"research::*"` → `research::query`); otherwise it's an exact match.
    """
    if allowed is None:
        return True
    for pat in allowed:
        if pat.endswith("*"):
            if agent_id.startswith(pat[:-1]):
                return True
        elif agent_id == pat:
            return True
    return False


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def require_principal(request: Request) -> Principal | None:
    """Authn dependency. Open mode → None; enforced mode → a verified `Principal`.

    Enforced when `oidc_issuer` or `api_keys` is configured. Tries the credential
    in `Authorization: Bearer <token>`: a JWT (when OIDC is on and the token is a
    three-segment JWT) is verified against the JWKS; otherwise it's matched against
    the static `api_keys`. Stashes the principal on `request.state.principal` for
    the handlers (AUTH-2). Raises 401 on a missing/invalid credential.
    """
    request.state.principal = None
    oidc_on = settings.oidc_issuer is not None
    if (not settings.api_keys and not oidc_on) or request.url.path in _OPEN_PATHS:
        return None  # open mode, or an always-public path

    token = _bearer(request)
    principal: Principal | None = None
    if token:
        if oidc_on and token.count(".") == 2:  # looks like a JWT → verify (raises 401)
            from .jwt_auth import verify_jwt  # local import keeps auth import-light

            principal = verify_jwt(token)
        elif (entry := settings.api_keys.get(token)) is not None:
            principal = Principal(
                tenant_id=entry.tenant_id,
                scope=entry.scope,
                agent_ids=tuple(entry.agent_ids) if entry.agent_ids is not None else None,
            )

    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="missing or invalid bearer credential",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.principal = principal
    return principal
