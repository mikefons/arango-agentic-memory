"""API authentication (DESIGN.md §17): static bearer keys → a `Principal`.

Keyless by default — when `settings.api_keys` is empty the core runs **open** (the
caller's body-asserted `tenant_id`/`access_level` are trusted, the dev/CI/demo
posture). When keys are configured, every `/v1` route requires
`Authorization: Bearer <key>`; an unknown/missing key is a `401`. The resolved
`Principal` is stashed on `request.state` for the handlers to derive identity from
(authz lives in the route layer, AUTH-2). `/health` is always exempt.

JWT/OIDC is a roadmap follow-on; this is the dependency-free, self-hostable default.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from ..config import settings

_OPEN_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


@dataclass(frozen=True)
class Principal:
    """An authenticated caller: the tenant it owns and what it may do."""

    tenant_id: str
    scope: str  # "read" | "write"


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def require_api_key(request: Request) -> Principal | None:
    """Authn dependency. Open mode → None; enforced mode → a verified `Principal`.

    Stashes the principal on `request.state.principal` so handlers can authorize
    against it (AUTH-2). Raises 401 in enforced mode on a missing/unknown key.
    """
    request.state.principal = None
    if not settings.api_keys or request.url.path in _OPEN_PATHS:
        return None  # open mode (no keys) or an always-public path

    key = _bearer(request)
    entry = settings.api_keys.get(key) if key else None
    if entry is None:
        raise HTTPException(
            status_code=401,
            detail="missing or invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    principal = Principal(tenant_id=entry.tenant_id, scope=entry.scope)
    request.state.principal = principal
    return principal
