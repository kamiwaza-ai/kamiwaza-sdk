"""Live coverage for the federation-brokered Keycloak IdP lifecycle.

Broker aliases beginning with ``federation-`` are internal mesh state. A real
two-cluster pair is therefore the only supported way for this test to create
one; operator IdP CRUD must never be used as a provisioning shortcut.

The receiver-realm pair path deliberately proves both Keycloak effects:

* the normal pair hook creates ``federation-<local federation id>`` as an OIDC
  provider on each cluster;
* the receiver-realm hook separately creates the receiver-owned realm.

The tests fail when the broker provider was not actually provisioned, so the
ENG-8422 public-list exclusion and ENG-8426 deletion assertions cannot pass
vacuously. They also exercise ENG-10173's POST, PUT, PATCH, and DELETE operator
guards against that real internal provider.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.auth import (
    GoogleConfig,
    RegisterIdPRequest,
    ToggleIdPRequest,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

logger = logging.getLogger(__name__)

_FED_PREFIX = "federation-"
_RESERVED_ALIAS_DETAIL = (
    'Aliases beginning with "federation-" are reserved for mesh federation brokers.'
)


@dataclass
class _OwnedSide:
    label: str
    client: Any
    federation_id: str | None = None


@dataclass
class _OwnedPair:
    name: str
    initiator: _OwnedSide
    receiver: _OwnedSide


def _idp_request(alias: str, *, credential: str) -> RegisterIdPRequest:
    return RegisterIdPRequest(
        provider="google",
        google=GoogleConfig(
            alias=alias,
            client_id=f"idp-lifecycle-{uuid.uuid4().hex[:8]}",
            client_secret=credential,
        ),
        ensure_redirects=False,
    )


def _admin_providers(client: Any) -> dict[str, Any]:
    providers = client.auth.list_identity_providers().providers
    return {provider.alias: provider for provider in providers}


def _required_broker_provider(client: Any, alias: str) -> Any:
    provider = _admin_providers(client).get(alias)
    assert provider is not None, (
        f"pairing did not provision brokered IdP {alias!r}; "
        "the lifecycle assertion would be vacuous"
    )
    assert provider.enabled is True, f"brokered IdP {alias!r} is unexpectedly disabled"
    return provider


def _federation_rows(client: Any) -> list[dict[str, Any]]:
    body = client._request("GET", "/cluster/federations")
    if isinstance(body, dict):
        body = body.get("items")
    assert isinstance(body, list), "federation listing did not return a list"
    assert all(
        isinstance(row, dict) for row in body
    ), "federation listing contained a non-object row"
    return body


def _owned_federation_ids(client: Any, name: str) -> set[str]:
    return {
        str(row["id"])
        for row in _federation_rows(client)
        if row.get("remote_cluster_name") == name and row.get("id")
    }


def _active_federation_ids(client: Any) -> set[str]:
    return {str(row["id"]) for row in _federation_rows(client) if row.get("id")}


def _cleanup_owned_side(side: _OwnedSide, name: str) -> None:
    """Delete only this run's rows and prove their broker IdPs disappeared."""
    delete_ids = _owned_federation_ids(side.client, name)
    known_ids = set(delete_ids)
    if side.federation_id:
        known_ids.add(side.federation_id)
        if side.federation_id in _active_federation_ids(side.client):
            delete_ids.add(side.federation_id)

    for federation_id in sorted(delete_ids):
        side.client._request(
            "DELETE",
            f"/cluster/federations/{federation_id}",
        )

    assert known_ids.isdisjoint(
        _active_federation_ids(side.client)
    ), f"{side.label} federation ids survived cleanup"
    assert not _owned_federation_ids(
        side.client, name
    ), f"{side.label} federation rows survived cleanup"
    aliases = set(_admin_providers(side.client))
    for federation_id in known_ids:
        alias = f"{_FED_PREFIX}{federation_id}"
        assert alias not in aliases, f"brokered IdP survived cleanup on {side.label}"


def _establish_owned_pair(pair: _OwnedPair, peer_url: str, psk: str) -> None:
    receiver = pair.receiver.client.federations.pair(
        name=pair.name,
        role="receiver",
        preshared_key=psk,
        realm_scope="per_federation",
    )
    pair.receiver.federation_id = str(receiver.id)
    initiator = pair.initiator.client.federations.pair(
        name=pair.name,
        role="initiator",
        remote_url=peer_url,
        preshared_key=psk,
        realm_scope="per_federation",
    )
    pair.initiator.federation_id = str(initiator.id)


@contextmanager
def _cleanup_preserving_primary(
    cleanup: Callable[[], None], label: str
) -> Iterator[None]:
    """Fail on cleanup alone without replacing an active primary failure."""
    primary_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            cleanup()
        except Exception:
            if primary_error is None:
                raise
            logger.warning("%s cleanup failed while preserving primary failure", label)


@pytest.fixture
def brokered_federation_pair(
    live_kamiwaza_session_client: KamiwazaClient,
    live_kamiwaza_peer_client: KamiwazaClient,
    live_peer_base_url: str,
) -> Iterator[_OwnedPair]:
    pair = _OwnedPair(
        name=f"eng8422-idp-live-{uuid.uuid4().hex[:10]}",
        initiator=_OwnedSide("initiator", live_kamiwaza_session_client),
        receiver=_OwnedSide("receiver", live_kamiwaza_peer_client),
    )
    cleanup = ExitStack()
    cleanup.callback(_cleanup_owned_side, pair.receiver, pair.name)
    cleanup.callback(_cleanup_owned_side, pair.initiator, pair.name)
    with _cleanup_preserving_primary(cleanup.close, "federation pair"):
        _establish_owned_pair(pair, live_peer_base_url, secrets.token_urlsafe(32))
        yield pair


def _assert_reserved_error(exc: APIError, method: str) -> None:
    assert exc.status_code == 400, f"reserved-alias {method} returned non-400"
    assert exc.response_data == {
        "detail": _RESERVED_ALIAS_DETAIL
    }, f"reserved-alias {method} returned the wrong refusal contract"


def _exercise_reserved_alias_mutations(client: Any, alias: str) -> None:
    """Prove every operator mutation refuses a real internal broker alias."""
    payload = _idp_request(alias, credential="not-a-real-credential")
    mutations = (
        ("POST", lambda: client.auth.register_identity_provider(payload)),
        ("PUT", lambda: client.auth.update_identity_provider(alias, payload)),
        (
            "PATCH",
            lambda: client.auth.toggle_identity_provider(
                alias,
                ToggleIdPRequest(enabled=False),
            ),
        ),
        ("DELETE", lambda: client.auth.delete_identity_provider(alias)),
    )
    for method, mutation in mutations:
        with pytest.raises(APIError) as exc_info:
            mutation()
        _assert_reserved_error(exc_info.value, method)
        _required_broker_provider(client, alias)


def _delete_control_idp(client: Any, alias: str) -> None:
    client.auth.delete_identity_provider(alias)
    assert alias not in _admin_providers(client), "control IdP survived cleanup"


@pytest.mark.requires_two_clusters
@pytest.mark.requires_receiver_realm
class TestBrokeredIdPLifecycle:
    def test_broker_idp_is_hidden_and_reserved_from_operator_mutation(
        self,
        brokered_federation_pair: _OwnedPair,
    ) -> None:
        """ENG-8422 + ENG-10173: a real broker stays internal and immutable."""
        side = brokered_federation_pair.receiver
        assert side.federation_id is not None
        alias = f"{_FED_PREFIX}{side.federation_id}"
        _required_broker_provider(side.client, alias)
        _exercise_reserved_alias_mutations(side.client, alias)

        control_alias = f"idp-lifecycle-control-{uuid.uuid4().hex[:8]}"
        credential = secrets.token_urlsafe(32)
        registration = side.client.auth.register_identity_provider(
            _idp_request(control_alias, credential=credential)
        )
        with _cleanup_preserving_primary(
            lambda: _delete_control_idp(side.client, control_alias),
            "control IdP",
        ):
            assert registration.idp_management_enabled is not False
            assert control_alias in _admin_providers(side.client)
            public_aliases = {
                provider.alias
                for provider in side.client.auth.list_public_identity_providers().providers
            }
            assert (
                control_alias in public_aliases
            ), "ordinary IdP missing from the freshly invalidated public listing"
            assert (
                alias not in public_aliases
            ), "federation-brokered IdP leaked into console login options"

    def test_deleting_federation_removes_its_brokered_idp(
        self,
        brokered_federation_pair: _OwnedPair,
    ) -> None:
        """ENG-8426: federation deletion removes the real internal OIDC IdP."""
        side = brokered_federation_pair.receiver
        assert side.federation_id is not None
        alias = f"{_FED_PREFIX}{side.federation_id}"
        _required_broker_provider(side.client, alias)

        side.client._request(
            "DELETE",
            f"/cluster/federations/{side.federation_id}",
        )

        assert side.federation_id not in _active_federation_ids(side.client)
        assert alias not in _admin_providers(
            side.client
        ), "brokered IdP survived federation deletion"
