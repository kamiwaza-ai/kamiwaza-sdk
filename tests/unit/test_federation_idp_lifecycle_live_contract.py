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


def _pair_fixture(
    initiator: _PairClient,
    receiver: _PairClient,
):
    return edge.brokered_federation_pair.__wrapped__(
        initiator,
        receiver,
        "https://peer.example/api",
    )


def _control_probe_pair() -> edge._OwnedPair:
    auth = Mock()
    auth.register_identity_provider.return_value = SimpleNamespace(
        idp_management_enabled=True
    )
    auth.list_public_identity_providers.return_value = SimpleNamespace(
        providers=[SimpleNamespace(alias="idp-lifecycle-control-abc12345")]
    )
    side = edge._OwnedSide(
        "receiver",
        SimpleNamespace(auth=auth),
        "receiver-id",
    )
    return edge._OwnedPair(
        name="owned-pair",
        initiator=edge._OwnedSide("initiator", Mock(), "initiator-id"),
        receiver=side,
    )


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


def test_pair_fixture_cleanup_failure_is_fatal_without_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_error = RuntimeError("pair cleanup failed")
    cleanup = Mock(side_effect=cleanup_error)
    monkeypatch.setattr(edge, "_cleanup_owned_side", cleanup)
    fixture = _pair_fixture(_PairClient("initiator-id"), _PairClient("receiver-id"))

    next(fixture)
    with pytest.raises(RuntimeError) as exc_info:
        next(fixture)

    assert exc_info.value is cleanup_error
    assert cleanup.call_count == 2


def test_pair_fixture_preserves_setup_failure_when_cleanup_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_error = ValueError("pair setup failed")
    cleanup_error = RuntimeError("pair cleanup failed")
    cleanup = Mock(side_effect=cleanup_error)
    initiator = _PairClient("initiator-id")
    initiator.federations.pair.side_effect = primary_error
    monkeypatch.setattr(edge, "_cleanup_owned_side", cleanup)
    fixture = _pair_fixture(initiator, _PairClient("receiver-id"))

    with pytest.raises(ValueError) as exc_info:
        next(fixture)

    assert exc_info.value is primary_error
    assert cleanup.call_count == 2


def test_pair_fixture_preserves_test_failure_when_cleanup_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_error = ValueError("test body failed")
    cleanup_error = RuntimeError("pair cleanup failed")
    cleanup = Mock(side_effect=cleanup_error)
    monkeypatch.setattr(edge, "_cleanup_owned_side", cleanup)
    fixture = _pair_fixture(_PairClient("initiator-id"), _PairClient("receiver-id"))
    next(fixture)

    with pytest.raises(ValueError) as exc_info:
        fixture.throw(primary_error)

    assert exc_info.value is primary_error
    assert cleanup.call_count == 2


def test_control_idp_cleanup_failure_is_fatal_without_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_error = RuntimeError("control IdP cleanup failed")
    cleanup = Mock(side_effect=cleanup_error)
    monkeypatch.setattr(edge.uuid, "uuid4", lambda: SimpleNamespace(hex="abc12345"))
    monkeypatch.setattr(edge, "_required_broker_provider", Mock())
    monkeypatch.setattr(edge, "_exercise_reserved_alias_mutations", Mock())
    monkeypatch.setattr(
        edge,
        "_admin_providers",
        Mock(return_value={"idp-lifecycle-control-abc12345": SimpleNamespace()}),
    )
    monkeypatch.setattr(edge, "_delete_control_idp", cleanup)

    with pytest.raises(RuntimeError) as exc_info:
        edge.TestBrokeredIdPLifecycle().test_broker_idp_is_hidden_and_reserved_from_operator_mutation(
            _control_probe_pair()
        )

    assert exc_info.value is cleanup_error
    cleanup.assert_called_once()


def test_control_idp_preserves_test_failure_when_cleanup_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_error = ValueError("control assertion failed")
    cleanup_error = RuntimeError("control IdP cleanup failed")
    cleanup = Mock(side_effect=cleanup_error)
    monkeypatch.setattr(edge.uuid, "uuid4", lambda: SimpleNamespace(hex="abc12345"))
    monkeypatch.setattr(edge, "_required_broker_provider", Mock())
    monkeypatch.setattr(edge, "_exercise_reserved_alias_mutations", Mock())
    monkeypatch.setattr(edge, "_admin_providers", Mock(side_effect=primary_error))
    monkeypatch.setattr(edge, "_delete_control_idp", cleanup)

    with pytest.raises(ValueError) as exc_info:
        edge.TestBrokeredIdPLifecycle().test_broker_idp_is_hidden_and_reserved_from_operator_mutation(
            _control_probe_pair()
        )

    assert exc_info.value is primary_error
    cleanup.assert_called_once()
