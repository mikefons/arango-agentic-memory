"""OIDC/JWT bearer verification + the authn dispatcher (DESIGN.md §17). No DB.

Self-signs RS256 tokens with a throwaway keypair and stubs the JWKS client, so the
full verification path (signature, alg, exp/iss/aud, claim mapping) is exercised
without a real IdP or network.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

import arango_memory.security.jwt_auth as jwt_auth
from arango_memory.config import ApiKeyEntry, settings
from arango_memory.security.auth import require_principal

_ISSUER = "https://issuer.test"
_AUDIENCE = "arango-memory"

_PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _sign(claims: dict[str, Any], *, key: rsa.RSAPrivateKey = _PRIV, alg: str = "RS256") -> str:
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "test"})


def _claims(**overrides: Any) -> dict[str, Any]:
    base = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": int(time.time()) + 300,
        "tenant_id": "acme",
        "scope": "read write",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _oidc_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Enable OIDC + stub the JWKS client to return our public key (no network)."""
    monkeypatch.setattr(settings, "oidc_issuer", _ISSUER)
    monkeypatch.setattr(settings, "oidc_audience", _AUDIENCE)
    fake_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=_PRIV.public_key())
    )
    monkeypatch.setattr(jwt_auth, "_jwks_client", lambda: fake_client)
    yield


# ── verify_jwt: claim mapping ─────────────────────────────
def test_valid_token_maps_to_principal() -> None:
    p = jwt_auth.verify_jwt(_sign(_claims()))
    assert p.tenant_id == "acme" and p.scope == "write"


def test_scope_read_only() -> None:
    assert jwt_auth.verify_jwt(_sign(_claims(scope="read"))).scope == "read"


def test_scope_from_roles_list() -> None:
    assert jwt_auth.verify_jwt(_sign(_claims(scope=["admin", "write"]))).scope == "write"


# ── verify_jwt: rejection matrix (all 401) ────────────────
@pytest.mark.parametrize(
    "token_factory",
    [
        pytest.param(lambda: _sign(_claims(exp=int(time.time()) - 120)), id="expired"),
        pytest.param(lambda: _sign(_claims(aud="someone-else")), id="wrong-audience"),
        pytest.param(lambda: _sign(_claims(iss="https://evil.test")), id="wrong-issuer"),
        pytest.param(lambda: _sign(_claims(), key=_OTHER), id="bad-signature"),
        pytest.param(lambda: _sign({k: v for k, v in _claims().items() if k != "tenant_id"}),
                     id="missing-tenant-claim"),
        pytest.param(lambda: _sign({k: v for k, v in _claims().items() if k != "exp"}),
                     id="missing-exp"),
    ],
)
def test_invalid_tokens_are_401(token_factory: Any) -> None:
    with pytest.raises(HTTPException) as exc:
        jwt_auth.verify_jwt(token_factory())
    assert exc.value.status_code == 401


# ── dispatcher (require_principal), no DB ─────────────────
def _request(path: str = "/v1/store", token: str | None = None) -> Any:
    headers = {"authorization": f"Bearer {token}"} if token else {}
    return SimpleNamespace(headers=headers, url=SimpleNamespace(path=path), state=SimpleNamespace())


def test_dispatcher_accepts_valid_jwt() -> None:
    req = _request(token=_sign(_claims()))
    principal = require_principal(req)
    assert principal is not None and principal.tenant_id == "acme"
    assert req.state.principal is principal


def test_dispatcher_rejects_invalid_jwt() -> None:
    with pytest.raises(HTTPException) as exc:
        require_principal(_request(token=_sign(_claims(aud="nope"))))
    assert exc.value.status_code == 401


def test_dispatcher_static_key_still_works_with_oidc_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # A non-JWT bearer falls through to the static-key table (coexistence).
    keys = {"k_static": ApiKeyEntry(tenant_id="acme", scope="read")}
    monkeypatch.setattr(settings, "api_keys", keys)
    principal = require_principal(_request(token="k_static"))
    assert principal is not None and principal.tenant_id == "acme" and principal.scope == "read"


def test_dispatcher_exempts_docs() -> None:
    assert require_principal(_request(path="/openapi.json")) is None
