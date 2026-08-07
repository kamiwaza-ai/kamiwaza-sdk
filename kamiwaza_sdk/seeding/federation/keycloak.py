"""Minimal Keycloak admin client for shared_idp dev seeding.

The platform API cannot create the shared *realm* / ROPC client / persona users /
clearance mapper — that is Keycloak-admin territory. This module is a small,
idempotent wrapper over the Keycloak Admin REST API covering exactly what the
``idp`` command group needs to stand up a shared_idp realm the way the L3 fixture
did by hand. It is intentionally dependency-light (``requests`` only) and
dev-scoped; production IdPs are managed by the customer's own tooling.

All operations are idempotent (ensure-semantics): safe to re-run.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

import requests  # type: ignore[import-untyped]


class KeycloakAdminError(RuntimeError):
    """A Keycloak admin REST call failed."""


class KeycloakAdmin:
    """Idempotent Keycloak-admin operations for shared_idp seeding.

    Args:
        base_url: Keycloak base, e.g. ``https://host/`` (the realms live at
            ``<base_url>/realms/<realm>``; the admin API at
            ``<base_url>/admin/realms/...``).
        admin_user / admin_password: master-realm admin credentials (the password
            is read from an env var by the CLI, never argv).
        verify: TLS verification (False for the dev self-signed cluster cert).
    """

    def __init__(
        self,
        base_url: str,
        *,
        admin_user: str,
        admin_password: str,
        verify: Any = True,
        timeout: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._admin_user = admin_user
        self._admin_password = admin_password
        self._verify = verify
        self._timeout = timeout
        self._token: Optional[str] = None

    # -- low-level ---------------------------------------------------------

    def _admin_token(self) -> str:
        if self._token:
            return self._token
        resp = requests.post(
            f"{self._base}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self._admin_user,
                "password": self._admin_password,
            },
            verify=self._verify,
            timeout=self._timeout,
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                f"admin token request failed ({resp.status_code}); check "
                "Keycloak URL + master-realm admin credentials"
            )
        token = str(resp.json()["access_token"])
        self._token = token
        return token

    def _req(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._admin_token()}"
        return requests.request(
            method,
            f"{self._base}/admin{path}",
            headers=headers,
            verify=self._verify,
            timeout=self._timeout,
            **kwargs,
        )

    @staticmethod
    def _created_id(resp: requests.Response) -> Optional[str]:
        """Extract the created entity id from a Keycloak 201 Location header."""
        loc = resp.headers.get("Location", "")
        return loc.rstrip("/").rsplit("/", 1)[-1] if loc else None

    @staticmethod
    def _ok_json(resp: requests.Response, what: str) -> Any:
        """Parse a successful admin-API JSON body, else raise a clear error.

        Guards against treating an error body (e.g. an ext-authz rejection dict
        on a gated /admin path) as data — which otherwise surfaces as an opaque
        KeyError/TypeError far from the real cause.
        """
        if resp.status_code >= 400:
            raise KeycloakAdminError(f"{what} failed ({resp.status_code}): {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError:
            return None

    # -- realm -------------------------------------------------------------

    def ensure_realm(self, realm: str) -> Dict[str, Any]:
        """Create the realm if absent (enabled). Idempotent."""
        got = self._req("GET", f"/realms/{quote(realm)}")
        if got.status_code == 200:
            return {"realm": realm, "created": False}
        resp = self._req(
            "POST", "/realms", json={"realm": realm, "enabled": True}
        )
        if resp.status_code not in (201, 409):
            raise KeycloakAdminError(
                f"create realm {realm!r} failed ({resp.status_code}): {resp.text[:200]}"
            )
        return {"realm": realm, "created": resp.status_code == 201}

    def set_unmanaged_attributes(self, realm: str, *, policy: str = "ENABLED") -> None:
        """Set the realm user-profile ``unmanagedAttributePolicy`` so persona
        custom attributes (e.g. ``clearance``) are accepted (ENG-4946).

        NOTE: this lives on the user-profile config endpoint, NOT the realm
        representation (setting it on the realm rep 400s "Unrecognized field").
        """
        path = f"/realms/{quote(realm)}/users/profile"
        got = self._req("GET", path)
        profile = got.json() if got.status_code == 200 else {}
        profile["unmanagedAttributePolicy"] = policy
        resp = self._req("PUT", path, json=profile)
        if resp.status_code not in (200, 204):
            raise KeycloakAdminError(
                f"set unmanagedAttributePolicy failed ({resp.status_code}): "
                f"{resp.text[:200]}"
            )

    # -- client ------------------------------------------------------------

    def ensure_ropc_client(self, realm: str, client_id: str) -> Dict[str, Any]:
        """Ensure a public, direct-access-grants (ROPC) client. Idempotent."""
        existing = self._ok_json(
            self._req(
                "GET",
                f"/realms/{quote(realm)}/clients",
                params={"clientId": client_id},
            ),
            "list clients",
        )
        if existing:
            return {"client_id": client_id, "id": existing[0]["id"], "created": False}
        resp = self._req(
            "POST",
            f"/realms/{quote(realm)}/clients",
            json={
                "clientId": client_id,
                "enabled": True,
                "publicClient": True,
                "directAccessGrantsEnabled": True,
                "standardFlowEnabled": False,
                "serviceAccountsEnabled": False,
            },
        )
        if resp.status_code not in (201, 409):
            raise KeycloakAdminError(
                f"create client {client_id!r} failed ({resp.status_code}): "
                f"{resp.text[:200]}"
            )
        return {"client_id": client_id, "id": self._created_id(resp), "created": True}

    def ensure_attribute_mapper(
        self, realm: str, client_uuid: str, *, attribute: str
    ) -> None:
        """Ensure a user-attribute -> access-token claim mapper on the client so a
        persona's ``clearance`` (etc.) is projected as a top-level token claim.
        """
        base = f"/realms/{quote(realm)}/clients/{client_uuid}/protocol-mappers/models"
        existing = self._ok_json(self._req("GET", base), "list protocol mappers") or []
        name = f"{attribute}-attr-mapper"
        if any(m.get("name") == name for m in existing):
            return
        resp = self._req(
            "POST",
            base,
            json={
                "name": name,
                "protocol": "openid-connect",
                "protocolMapper": "oidc-usermodel-attribute-mapper",
                "config": {
                    "user.attribute": attribute,
                    "claim.name": attribute,
                    "jsonType.label": "String",
                    "id.token.claim": "true",
                    "access.token.claim": "true",
                    "userinfo.token.claim": "true",
                },
            },
        )
        if resp.status_code not in (201, 409):
            raise KeycloakAdminError(
                f"create attribute mapper {name!r} failed ({resp.status_code}): "
                f"{resp.text[:200]}"
            )

    # -- user --------------------------------------------------------------

    def ensure_user(
        self,
        realm: str,
        username: str,
        *,
        password: str,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Ensure a persona user with attributes + a non-temporary password.

        email / first / last are set so the direct-access-grant (ROPC) is not
        blocked by "Account is not fully set up".
        """
        found = self._ok_json(
            self._req(
                "GET",
                f"/realms/{quote(realm)}/users",
                params={"username": username, "exact": "true"},
            ),
            "search users",
        )
        attrs = {k: (v if isinstance(v, list) else [str(v)]) for k, v in (attributes or {}).items()}
        rep = {
            "username": username,
            "enabled": True,
            "emailVerified": True,
            "email": f"{username}@shared.local",
            "firstName": username,
            "lastName": "persona",
            "attributes": attrs,
        }
        if found:
            uid = found[0]["id"]
            self._req("PUT", f"/realms/{quote(realm)}/users/{uid}", json=rep)
            created = False
        else:
            resp = self._req("POST", f"/realms/{quote(realm)}/users", json=rep)
            if resp.status_code not in (201, 409):
                raise KeycloakAdminError(
                    f"create user {username!r} failed ({resp.status_code}): "
                    f"{resp.text[:200]}"
                )
            uid = self._created_id(resp)
            created = True
        pw = self._req(
            "PUT",
            f"/realms/{quote(realm)}/users/{uid}/reset-password",
            json={"type": "password", "value": password, "temporary": False},
        )
        if pw.status_code not in (200, 204):
            raise KeycloakAdminError(
                f"set password for {username!r} failed ({pw.status_code})"
            )
        return {"username": username, "id": uid, "created": created}

    # -- token (test helper) ----------------------------------------------

    def ropc_token(
        self, realm: str, client_id: str, username: str, password: str
    ) -> str:
        """Mint an access token for a persona via ROPC (direct access grant)."""
        resp = requests.post(
            f"{self._base}/realms/{quote(realm)}/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": client_id,
                "username": username,
                "password": password,
            },
            verify=self._verify,
            timeout=self._timeout,
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                f"ROPC token for {username!r} failed ({resp.status_code}): "
                f"{resp.text[:200]}"
            )
        return str(resp.json().get("access_token", ""))

    def issuer_url(self, realm: str) -> str:
        """The realm's OIDC issuer — the shared_issuer for ``fed pair``."""
        return f"{self._base}/realms/{realm}"


def jwks_uri_from_issuer(issuer: str) -> str:
    """Derive a Keycloak realm's JWKS endpoint (mirrors the server-side helper;
    ``fed pair`` uses this so JWKS is pinned to the issuer — #2298 H1)."""
    return issuer.rstrip("/") + "/protocol/openid-connect/certs"
