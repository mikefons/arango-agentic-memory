"""Configure a Keycloak realm as the authorization server for the MCP server's OAuth mode (MCP-1).

Reference setup for docs/adapters/mcp.md, via Keycloak's admin REST API. It creates:

- a realm (default `memory`) whose user profile allows a `tenant_id` user attribute;
- a `memory` client scope, a realm *default* scope (so dynamically registered MCP clients get
  it) that adds the MCP resource URL to the token audience and copies the user's `tenant_id`
  into the `tenant_id` claim;
- realm roles `memory-writer` / `memory-consolidator`, mapped into a `memory_access` claim by a
  separate `memory-roles` default scope (see the note in `configure` for why it's separate). Write
  access is a *user entitlement* granted by an admin — run the MCP server with
  `--oauth-scope-claim memory_access`. (A scope a client could simply request would let any
  dynamically registered client ask for write.)
- anonymous dynamic client registration allowed for the hosts given in `--trusted-hosts`;
- optionally a test user with a `tenant_id` attribute.

Local/dev use: `--dev` also drops the anonymous "consent required" registration policy so a
headless test can log in without a consent screen. Keep consent on in production.

Usage:
  KEYCLOAK_ADMIN_PASSWORD=… python scripts/keycloak_mcp_realm.py \\
    --keycloak http://127.0.0.1:18080 --resource-url https://mcp.example.com/mcp \\
    [--user alice --tenant acme --role memory-writer]   # password from KEYCLOAK_USER_PASSWORD
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import httpx


class Admin:
    def __init__(self, base: str, password: str, user: str = "admin") -> None:
        self.base = base.rstrip("/")
        token = httpx.post(
            f"{self.base}/realms/master/protocol/openid-connect/token",
            data={"grant_type": "password", "client_id": "admin-cli",
                  "username": user, "password": password},
        )
        token.raise_for_status()
        self.http = httpx.Client(
            base_url=f"{self.base}/admin/realms",
            headers={"authorization": f"Bearer {token.json()['access_token']}"},
        )

    def call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        res = self.http.request(method, path, **kw)
        if res.status_code >= 400 and res.status_code != 409:  # 409: already exists → idempotent
            sys.exit(f"{method} {path} → {res.status_code} {res.text[:300]}")
        return res


def _scope_id(admin: Admin, realm: str, name: str) -> str:
    scopes = admin.call("GET", f"/{realm}/client-scopes").json()
    return next(s["id"] for s in scopes if s["name"] == name)


def configure(admin: Admin, *, realm: str, resource_url: str, trusted_hosts: list[str],
              dev: bool) -> None:
    admin.call("POST", "", json={"realm": realm, "enabled": True})

    profile = admin.call("GET", f"/{realm}/users/profile").json()
    profile["unmanagedAttributePolicy"] = "ADMIN_EDIT"  # admins set tenant_id; users can't
    admin.call("PUT", f"/{realm}/users/profile", json=profile)

    admin.call("POST", f"/{realm}/client-scopes", json={
        "name": "memory", "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "true"},
        "protocolMappers": [
            {"name": "mcp-audience", "protocol": "openid-connect",
             "protocolMapper": "oidc-audience-mapper",
             "config": {"included.custom.audience": resource_url,
                        "access.token.claim": "true", "id.token.claim": "false"}},
            {"name": "tenant-id", "protocol": "openid-connect",
             "protocolMapper": "oidc-usermodel-attribute-mapper",
             "config": {"user.attribute": "tenant_id", "claim.name": "tenant_id",
                        "jsonType.label": "String", "access.token.claim": "true",
                        "id.token.claim": "false", "userinfo.token.claim": "false"}},
        ],
    })
    # Roles live in their own scope. Dynamically registered clients don't get "full scope", so a
    # role reaches the token only if a client scope is scope-mapped to it — and Keycloak applies
    # a scope that has role mappings *only for users holding one of those roles*. Mapping them
    # onto `memory` would strip the audience + tenant from every role-less user's token.
    admin.call("POST", f"/{realm}/client-scopes", json={
        "name": "memory-roles", "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "false"},
        "protocolMappers": [
            {"name": "memory-access", "protocol": "openid-connect",
             "protocolMapper": "oidc-usermodel-realm-role-mapper",
             "config": {"claim.name": "memory_access", "multivalued": "true",
                        "jsonType.label": "String", "access.token.claim": "true",
                        "id.token.claim": "false", "userinfo.token.claim": "false"}},
        ],
    })
    roles = []
    for role in ("memory-writer", "memory-consolidator"):
        admin.call("POST", f"/{realm}/roles", json={"name": role})
        roles.append(admin.call("GET", f"/{realm}/roles/{role}").json())
    admin.call("POST", f"/{realm}/client-scopes/{_scope_id(admin, realm, 'memory-roles')}"
                       "/scope-mappings/realm", json=roles)
    for scope in ("memory", "memory-roles"):
        scope_id = _scope_id(admin, realm, scope)
        admin.call("PUT", f"/{realm}/default-default-client-scopes/{scope_id}")

    # Anonymous dynamic client registration policies (Keycloak gates DCR with these).
    components = admin.call(
        "GET", f"/{realm}/components",
        params={"type": "org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy"},
    ).json()
    for comp in components:
        if comp.get("subType") != "anonymous":
            continue
        if comp["providerId"] == "trusted-hosts":
            comp["config"]["trusted-hosts"] = trusted_hosts
            comp["config"]["host-sending-registration-request-must-match"] = ["false"]
            comp["config"]["client-uris-must-match"] = ["true"]
            admin.call("PUT", f"/{realm}/components/{comp['id']}", json=comp)
        elif comp["providerId"] == "allowed-client-templates":
            comp["config"]["allowed-client-scopes"] = ["memory", "memory-roles"]
            admin.call("PUT", f"/{realm}/components/{comp['id']}", json=comp)
        elif comp["providerId"] == "consent-required" and dev:
            admin.call("DELETE", f"/{realm}/components/{comp['id']}")


def create_user(admin: Admin, *, realm: str, username: str, password: str, tenant: str,
                role: str | None) -> None:
    admin.call("POST", f"/{realm}/users", json={
        "username": username, "enabled": True, "emailVerified": True,
        "email": f"{username}@example.test", "firstName": username, "lastName": "Test",
        "attributes": {"tenant_id": [tenant]},
        "credentials": [{"type": "password", "value": password, "temporary": False}],
    })
    if role:
        user_id = admin.call("GET", f"/{realm}/users", params={"username": username,
                                                               "exact": "true"}).json()[0]["id"]
        rep = admin.call("GET", f"/{realm}/roles/{role}").json()
        admin.call("POST", f"/{realm}/users/{user_id}/role-mappings/realm", json=[rep])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--keycloak", required=True)
    p.add_argument("--realm", default="memory")
    p.add_argument("--resource-url", required=True, help="the MCP server's public /mcp URL")
    p.add_argument("--trusted-hosts", default="localhost,127.0.0.1",
                   help="hosts allowed in anonymously registered redirect URIs")
    p.add_argument("--dev", action="store_true", help="drop the anonymous consent policy")
    p.add_argument("--user")
    p.add_argument("--tenant")
    p.add_argument("--role", choices=("memory-writer", "memory-consolidator"),
                   help="grant the test user write (or consolidate) access")
    args = p.parse_args()

    admin = Admin(args.keycloak, os.environ["KEYCLOAK_ADMIN_PASSWORD"],
                  os.environ.get("KEYCLOAK_ADMIN_USER", "admin"))
    configure(admin, realm=args.realm, resource_url=args.resource_url,
              trusted_hosts=[h.strip() for h in args.trusted_hosts.split(",") if h.strip()],
              dev=args.dev)
    if args.user:
        if not args.tenant:
            sys.exit("--user needs --tenant")
        create_user(admin, realm=args.realm, username=args.user,
                    password=os.environ["KEYCLOAK_USER_PASSWORD"], tenant=args.tenant,
                    role=args.role)
    print(f"issuer: {args.keycloak.rstrip('/')}/realms/{args.realm}")


if __name__ == "__main__":
    main()
