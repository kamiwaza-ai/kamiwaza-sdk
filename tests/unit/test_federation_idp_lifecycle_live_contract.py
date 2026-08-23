"""Offline contracts for the live federation IdP lifecycle probe."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from kamiwaza_sdk.exceptions import APIError
from tests.integration import test_federation_idp_lifecycle_live as edge

pytestmark = pytest.mark.unit


class _Federations:
    def __init__(self, federation_id: str) -> None:
        self.federation_id = federation_id
        self.pair = Mock(return_value=SimpleNamespace(id=federation_id))


class _PairClient:
    def __init__(self, federation_id: str) -> None:
        self.federations = _Federations(federation_id)


class _Auth:
    def __init__(
        self, alias: str, *, leave_provider_after_delete: bool = False
    ) -> None:
        self.alias = alias
        self.leave_provider_after_delete = leave_provider_after_delete
        self.providers = {
            alias: SimpleNamespace(alias=alias, provider_id="oidc", enabled=True)
        }
        self.mutations: list[str] = []

    @staticmethod
    def _reserved_error() -> APIError:
        return APIError(
            "reserved alias",
            status_code=400,
            response_data={"detail": edge._RESERVED_ALIAS_DETAIL},
        )

    def register_identity_provider(self, _payload):
        self.mutations.append("POST")
        raise self._reserved_error()

    def update_identity_provider(self, _alias, _payload):
        self.mutations.append("PUT")
        raise self._reserved_error()

    def toggle_identity_provider(self, _alias, _payload):
        self.mutations.append("PATCH")
        raise self._reserved_error()

    def delete_identity_provider(self, alias):
        self.mutations.append("DELETE")
        raise self._reserved_error()

    def list_identity_providers(self):
        return SimpleNamespace(providers=list(self.providers.values()))


class _CleanupClient:
    def __init__(
        self,
        name: str,
        federation_id: str,
        *,
        leave_provider_after_delete: bool = False,
    ) -> None:
        self.rows = [
            {
                "id": federation_id,
                "remote_cluster_name": name,
                "status": "PAIRED",
            }
        ]
        self.auth = _Auth(
            f"federation-{federation_id}",
            leave_provider_after_delete=leave_provider_after_delete,
        )
        self.deleted: list[str] = []

    def _request(self, method, path, **_kwargs):
        if method == "GET" and path == "/cluster/federations":
            return list(self.rows)
        if method == "DELETE":
            federation_id = path.rsplit("/", 1)[-1]
            self.deleted.append(federation_id)
            self.rows = [row for row in self.rows if row["id"] != federation_id]
            if not self.auth.leave_provider_after_delete:
                self.auth.providers.pop(f"federation-{federation_id}", None)
            return {"deleted": True}
        raise AssertionError(f"unexpected request: {method} {path}")


def test_establish_pair_uses_supported_receiver_realm_pairing() -> None:
    initiator = _PairClient("initiator-id")
    receiver = _PairClient("receiver-id")
    pair = edge._OwnedPair(
        name="owned-pair",
        initiator=edge._OwnedSide("initiator", initiator),
        receiver=edge._OwnedSide("receiver", receiver),
    )

    edge._establish_owned_pair(pair, "https://peer.example/api", "opaque-psk")

    assert pair.initiator.federation_id == "initiator-id"
    assert pair.receiver.federation_id == "receiver-id"
    receiver.federations.pair.assert_called_once_with(
        name="owned-pair",
        role="receiver",
        preshared_key="opaque-psk",
        realm_scope="per_federation",
    )
    initiator.federations.pair.assert_called_once_with(
        name="owned-pair",
        role="initiator",
        remote_url="https://peer.example/api",
        preshared_key="opaque-psk",
        realm_scope="per_federation",
    )


def test_reserved_alias_probe_exercises_every_operator_mutation() -> None:
    alias = "federation-receiver-id"
    auth = _Auth(alias)
    client = SimpleNamespace(auth=auth)

    edge._exercise_reserved_alias_mutations(client, alias)

    assert auth.mutations == ["POST", "PUT", "PATCH", "DELETE"]
    provider = auth.providers[alias]
    assert provider.provider_id == "oidc"
    assert provider.enabled is True


def test_owned_cleanup_deletes_the_row_and_verifies_internal_idp_absence() -> None:
    name = "owned-pair"
    federation_id = "receiver-id"
    client = _CleanupClient(name, federation_id)
    side = edge._OwnedSide("receiver", client, federation_id)

    edge._cleanup_owned_side(side, name)

    assert client.deleted == [federation_id]
    assert client.rows == []
    assert client.auth.providers == {}


def test_owned_cleanup_uses_known_id_when_receiver_adopts_peer_name() -> None:
    federation_id = "receiver-id"
    client = _CleanupClient("initiator-cluster-name", federation_id)
    side = edge._OwnedSide("receiver", client, federation_id)

    edge._cleanup_owned_side(side, "original-owned-pair-name")

    assert client.deleted == [federation_id]
    assert client.rows == []
    assert client.auth.providers == {}


def test_owned_cleanup_fails_closed_when_internal_idp_survives() -> None:
    name = "owned-pair"
    federation_id = "receiver-id"
    client = _CleanupClient(
        name,
        federation_id,
        leave_provider_after_delete=True,
    )
    side = edge._OwnedSide("receiver", client, federation_id)

    with pytest.raises(AssertionError, match="brokered IdP survived cleanup"):
        edge._cleanup_owned_side(side, name)

    assert client.deleted == [federation_id]


def test_reserved_alias_probe_checks_provider_after_each_rejection() -> None:
    alias = "federation-receiver-id"
    auth = _Auth(alias)
    original_list = auth.list_identity_providers
    auth.list_identity_providers = Mock(side_effect=original_list)
    client = SimpleNamespace(auth=auth)

    edge._exercise_reserved_alias_mutations(client, alias)

    assert auth.list_identity_providers.call_args_list == [call()] * 4
