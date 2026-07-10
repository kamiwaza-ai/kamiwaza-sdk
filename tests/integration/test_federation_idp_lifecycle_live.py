"""Live smoke coverage for the federation IdP lifecycle (single-cluster).

Two mainline bugs fixed in kamiwaza, guarded here at the SDK/API level:

  - ENG-8422: federation-brokered IdPs (alias ``federation-<...>``) are mesh
    data-plane trust, not console SSO -- they must NOT appear in the public
    login provider list (``GET /auth/idp/public/providers``), even though they
    stay enabled for brokered peer-JWT validation.
  - ENG-8426: deleting a federation removes its brokered OIDC IdP
    (``federation-<federation_id>``), revoking the receiver's Keycloak trust so
    the IdP does not orphan and a deleted peer realm can no longer broker logins.

Both run against a single live cluster (no peer required). They exercise the
API/UI-facing contract; the fix wiring is also covered by kamiwaza unit tests.
"""

from __future__ import annotations

import uuid

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.auth import GoogleConfig, RegisterIdPRequest

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

_FED_PREFIX = "federation-"


def _register_throwaway_idp(client, alias: str):
    """Register a throwaway IdP under ``alias``.

    Uses the ``google`` provider so Keycloak does not fetch external OIDC
    discovery metadata -- both the public-login filter and the delete teardown
    key off the alias prefix, not the provider.
    """
    return client.auth.register_identity_provider(
        RegisterIdPRequest(
            provider="google",
            google=GoogleConfig(
                alias=alias,
                client_id=f"smoke-{uuid.uuid4().hex[:8]}",
                client_secret=uuid.uuid4().hex,
            ),
            ensure_redirects=False,
        )
    )


def _delete_idp_quiet(client, alias: str) -> None:
    try:
        client.auth.delete_identity_provider(alias)
    except APIError:
        pass


def _delete_federation_quiet(client, fed_id: str) -> None:
    try:
        client._request("DELETE", f"/cluster/federations/{fed_id}")
    except APIError:
        pass


def test_federation_idps_never_offered_as_console_login(live_kamiwaza_client) -> None:
    """ENG-8422: a ``federation-<...>`` IdP must NOT surface in the public login
    provider list, while an ordinary admin-configured IdP does."""
    client = live_kamiwaza_client
    suffix = uuid.uuid4().hex[:8]
    fed_alias = f"{_FED_PREFIX}smoke-{suffix}"
    ctl_alias = f"smoke-login-ctl-{suffix}"

    reg = _register_throwaway_idp(client, fed_alias)
    if reg.idp_management_enabled is False:
        pytest.skip("IdP management is disabled on this platform")
    try:
        _register_throwaway_idp(client, ctl_alias)

        # Guard against a vacuous pass: the exclusion assertion below is
        # meaningless if the federation IdP never actually registered. The
        # admin listing surfaces every IdP regardless of login-page visibility,
        # so it confirms both were really created.
        admin_aliases = {
            p.alias for p in client.auth.list_identity_providers().providers
        }
        assert fed_alias in admin_aliases, (
            f"federation IdP {fed_alias!r} was not created -- the exclusion "
            f"check would pass vacuously"
        )
        assert ctl_alias in admin_aliases, f"control IdP {ctl_alias!r} was not created"

        # The public login list (GET /auth/idp/public/providers) excludes the
        # federation IdP but keeps the ordinary control.
        public_aliases = {
            p.alias for p in client.auth.list_public_identity_providers().providers
        }
        assert ctl_alias in public_aliases, (
            "an ordinary admin-configured IdP should be offered as a login option"
        )
        assert fed_alias not in public_aliases, (
            f"federation-brokered IdP {fed_alias!r} leaked into console login "
            f"options: {sorted(public_aliases)}"
        )
        assert not any(a.startswith(_FED_PREFIX) for a in public_aliases), (
            f"no federation-* IdP may appear in public login providers: "
            f"{sorted(public_aliases)}"
        )
    finally:
        _delete_idp_quiet(client, fed_alias)
        _delete_idp_quiet(client, ctl_alias)


def test_deleting_federation_removes_its_brokered_idp(live_kamiwaza_client) -> None:
    """ENG-8426: deleting a federation removes its brokered OIDC IdP
    (``federation-<federation_id>``), revoking the receiver's Keycloak trust."""
    client = live_kamiwaza_client
    name = f"uat-idp-teardown-{uuid.uuid4().hex[:8]}"

    # Admitted single-cluster create: a shared_idp receiver row (no peer needed).
    created = client._request(
        "POST",
        "/cluster/federations",
        json={
            "remote_cluster_name": name,
            "role": "receiver",
            "preshared_key": str(uuid.uuid4()),
            "shared_issuer_url": "https://shared.example/realms/federated",
        },
    )
    assert isinstance(created, dict) and created.get("id"), (
        f"federation create did not return an id: {created!r}"
    )
    fed_id = created["id"]
    idp_alias = f"{_FED_PREFIX}{fed_id}"

    # Guard the created federation from here on -- if IdP registration below
    # raises (permissions / Keycloak validation), the federation must still be
    # cleaned up in finally rather than orphaned on the live cluster.
    try:
        reg = _register_throwaway_idp(client, idp_alias)
        if reg.idp_management_enabled is False:
            pytest.skip("IdP management is disabled on this platform")

        # Precondition: the brokered IdP exists before the federation is deleted.
        before = {
            p.alias for p in client.auth.list_identity_providers().providers
        }
        assert idp_alias in before, "precondition: the brokered IdP should exist"

        # Deleting the federation must revoke the trust by removing the IdP.
        client._request("DELETE", f"/cluster/federations/{fed_id}")

        after = {
            p.alias for p in client.auth.list_identity_providers().providers
        }
        assert idp_alias not in after, (
            f"brokered IdP {idp_alias!r} survived federation delete "
            f"(stale Keycloak trust in a deleted peer realm)"
        )
    finally:
        _delete_idp_quiet(client, idp_alias)
        _delete_federation_quiet(client, fed_id)
