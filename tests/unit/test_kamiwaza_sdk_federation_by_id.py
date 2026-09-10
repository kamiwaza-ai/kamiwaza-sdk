"""ENG-10178 public federation-by-ID proxy contracts."""

from __future__ import annotations

import uuid

import pytest

from tests.unit.test_kamiwaza_sdk_services_federations import _MockClient

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "federation_id",
    [None, "", "   ", "not-a-uuid", "../peer", 123],
)
def test_by_id_rejects_invalid_federation_id(federation_id: object | None) -> None:
    """The local proxy rejects IDs that could only form malformed routes."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    with pytest.raises(ValueError, match="federation_id must be a valid UUID"):
        FederationsAPI(_MockClient()).by_id(federation_id)


def test_by_id_guest_operations_use_exact_federation() -> None:
    """TS-1: receiver guest operations need no discovery request."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    federation_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    federation_id = str(federation_uuid)
    client.expect(
        "POST",
        f"/cluster/federations/{federation_id}/guests",
        {
            "external_id": "carol@source",
            "realm": f"federation-{federation_id}",
            "offline_token": "OFFLINE-CREDENTIAL",
        },
    )
    client.expect(
        "POST",
        f"/cluster/federations/{federation_id}/guests/carol@source/revoke",
        {"success": True},
    )

    proxy = FederationsAPI(client).by_id(federation_uuid)
    guest = proxy.guests.enroll("carol@source")
    revoked = proxy.guests.revoke("carol@source")

    assert guest.realm == f"federation-{federation_id}"
    assert revoked == {"success": True}
    assert [(method, path) for method, path, _ in client.calls] == [
        ("POST", f"/cluster/federations/{federation_id}/guests"),
        (
            "POST",
            f"/cluster/federations/{federation_id}/guests/carol@source/revoke",
        ),
    ]


def test_by_id_disconnect_uses_exact_federation() -> None:
    """TS-2: disconnect uses the supplied ID without a lookup."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    federation_id = "22222222-2222-2222-2222-222222222222"
    client.expect(
        "POST",
        f"/cluster/federations/{federation_id}/disconnect",
        {"message": "disconnected"},
    )

    result = FederationsAPI(client).by_id(federation_id).disconnect(force=True)

    assert result == {"message": "disconnected"}
    assert client.calls == [
        (
            "POST",
            f"/cluster/federations/{federation_id}/disconnect",
            {"params": {"force": "true"}},
        ),
    ]


def test_by_id_probe_uses_explicit_remote_name_and_credential(monkeypatch) -> None:
    """TS-3: callers can retain a name-keyed mesh credential without a lookup."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    federation_id = "33333333-3333-3333-3333-333333333333"
    remote_name = "initiator-advertised"
    client.expect(
        "GET",
        f"/mesh/{remote_name}/api/cluster/cluster_capabilities",
        {"system_type": "linux", "os": "linux"},
    )
    client.expect(
        "POST",
        f"/cluster/federations/{federation_id}/disconnect",
        {"message": "disconnected"},
    )
    monkeypatch.setenv(
        "KAMIWAZA_FEDERATION_CREDENTIAL_INITIATOR_ADVERTISED",
        "OFFLINE-CREDENTIAL",
    )

    proxy = FederationsAPI(client).by_id(
        federation_id,
        remote_name=remote_name,
    )
    capabilities = proxy.probe()
    disconnected = proxy.disconnect()

    assert capabilities.system_type == "linux"
    assert disconnected == {"message": "disconnected"}
    assert client.calls == [
        (
            "GET",
            f"/mesh/{remote_name}/api/cluster/cluster_capabilities",
            {"headers": {"X-KZ-Federation-Credential": "OFFLINE-CREDENTIAL"}},
        ),
        (
            "POST",
            f"/cluster/federations/{federation_id}/disconnect",
            {"params": None},
        ),
    ]


def test_by_id_probe_defaults_to_id_selector_and_credential(monkeypatch) -> None:
    """TS-4: an omitted remote name has an explicit UUID-keyed fallback."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    federation_id = "44444444-4444-4444-4444-444444444444"
    client.expect(
        "GET",
        f"/mesh/{federation_id}/api/cluster/cluster_capabilities",
        {"system_type": "linux", "os": "linux"},
    )
    monkeypatch.setenv(
        "KAMIWAZA_FEDERATION_CREDENTIAL_44444444_4444_4444_4444_444444444444",
        "UUID-KEYED-CREDENTIAL",
    )

    capabilities = FederationsAPI(client).by_id(federation_id).probe()

    assert capabilities.system_type == "linux"
    assert client.calls == [
        (
            "GET",
            f"/mesh/{federation_id}/api/cluster/cluster_capabilities",
            {"headers": {"X-KZ-Federation-Credential": "UUID-KEYED-CREDENTIAL"}},
        )
    ]
