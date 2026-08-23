"""Live smoke coverage for operator-facing federation IdP boundaries.

The operator IdP API cannot create, update, toggle, or delete aliases beginning
with ``federation-``. Those aliases belong exclusively to Core's internal mesh
broker path (ENG-10173). This single-cluster smoke therefore exercises the
public contract it can truthfully reach:

* an ordinary admin-configured IdP appears in both admin and public listings;
* an operator attempt to register a reserved broker alias fails closed.

Core unit tests own the internal-only assertions: ``test_idp_api.py`` verifies
that broker IdPs are excluded from the public listing (ENG-8422), and
``test_federation.py`` verifies that federation deletion unregisters its broker
IdP (ENG-8426). Manufacturing an internal broker through the public admin API
would bypass the operator boundary these live tests must preserve.
"""

from __future__ import annotations

import uuid

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.auth import GoogleConfig, RegisterIdPRequest

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

_FED_PREFIX = "federation-"
_RESERVED_ALIAS_DETAIL = (
    'Aliases beginning with "federation-" are reserved for mesh federation brokers.'
)


def _register_throwaway_idp(client, alias: str):
    """Register a throwaway IdP under ``alias``.

    Uses the ``google`` provider so Keycloak does not fetch external OIDC
    discovery metadata. Reserved aliases are expected to fail before Keycloak
    is mutated.
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


def test_ordinary_idp_is_offered_as_console_login(live_kamiwaza_client) -> None:
    """ENG-8422: the public filter keeps ordinary login providers visible."""
    client = live_kamiwaza_client
    suffix = uuid.uuid4().hex[:8]
    ctl_alias = f"smoke-login-ctl-{suffix}"

    reg = _register_throwaway_idp(client, ctl_alias)
    try:
        if reg.idp_management_enabled is False:
            pytest.skip("IdP management is disabled on this platform")

        admin_aliases = {
            p.alias for p in client.auth.list_identity_providers().providers
        }
        assert ctl_alias in admin_aliases, f"control IdP {ctl_alias!r} was not created"

        public_aliases = {
            p.alias for p in client.auth.list_public_identity_providers().providers
        }
        assert (
            ctl_alias in public_aliases
        ), "an ordinary admin-configured IdP should be offered as a login option"
        assert not any(a.startswith(_FED_PREFIX) for a in public_aliases), (
            f"no federation-* IdP may appear in public login providers: "
            f"{sorted(public_aliases)}"
        )
    finally:
        _delete_idp_quiet(client, ctl_alias)


def test_operator_cannot_register_reserved_federation_alias(
    live_kamiwaza_client,
) -> None:
    """ENG-10173: operator registration cannot claim an internal broker alias."""
    client = live_kamiwaza_client
    alias = f"{_FED_PREFIX}operator-smoke-{uuid.uuid4().hex[:8]}"

    try:
        with pytest.raises(APIError) as exc_info:
            _register_throwaway_idp(client, alias)

        error = exc_info.value
        assert error.status_code == 400
        assert error.response_data == {"detail": _RESERVED_ALIAS_DETAIL}

        admin_aliases = {
            p.alias for p in client.auth.list_identity_providers().providers
        }
        assert (
            alias not in admin_aliases
        ), f"reserved alias {alias!r} was created despite the rejected request"
    finally:
        # Cleans up if this test runs against a regressed server that accepts
        # the registration. Hardened servers reject this delete too.
        _delete_idp_quiet(client, alias)
