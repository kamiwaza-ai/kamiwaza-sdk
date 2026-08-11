"""Receiver-realm federation pairing and guest-management contracts."""

from __future__ import annotations

import pytest

from tests.unit.test_kamiwaza_sdk_services_federations import (
    _MockClient,
    _create_call,
    _stage_pair_responses,
)

pytestmark = pytest.mark.unit


def test_pair_forwards_realm_scope_when_supplied() -> None:
    """The receiver-realm scope reaches the federation create body."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    _stage_pair_responses(client)

    api = FederationsAPI(client)
    api.pair(
        name="ORION",
        role="receiver",
        remote_url="https://orion.example.com",
        realm_scope="per_federation",
    )

    _, body = _create_call(client)
    assert body["realm_scope"] == "per_federation"


def test_pair_omits_realm_scope_when_not_supplied() -> None:
    """The default create body remains free of receiver-realm settings."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    _stage_pair_responses(client)

    api = FederationsAPI(client)
    api.pair(name="ORION", role="initiator", remote_url="https://orion.example.com")

    _, body = _create_call(client)
    assert "realm_scope" not in body


def _stage_guest_federation(client: _MockClient, *, fid: str) -> None:
    """Stage the name-to-id lookup used by guest operations."""
    client.expect(
        "GET",
        "/cluster/federations",
        [{"id": fid, "status": "PAIRED", "remote_cluster_name": "ORION"}],
    )


def test_guests_property_returns_guests_api() -> None:
    from kamiwaza_sdk.services.federations import (
        FederationGuestsAPI,
        FederationsAPI,
    )

    client = _MockClient()
    guests = FederationsAPI(client)["ORION"].guests
    assert isinstance(guests, FederationGuestsAPI)


def test_guests_enroll_posts_and_returns_credential() -> None:
    """Enrollment returns the receiver-minted one-time credential."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    fid = "11111111-1111-1111-1111-111111111111"
    _stage_guest_federation(client, fid=fid)
    client.expect(
        "POST",
        f"/cluster/federations/{fid}/guests",
        {
            "external_id": "carol@src-uuid",
            "realm": f"federation-{fid}",
            "offline_token": "OFFLINE-CRED-XYZ",
        },
    )

    guest = FederationsAPI(client)["ORION"].guests.enroll("carol@src-uuid")

    assert guest.external_id == "carol@src-uuid"
    assert guest.realm == f"federation-{fid}"
    assert guest.offline_token == "OFFLINE-CRED-XYZ"
    post = [(p, kw) for m, p, kw in client.calls if m == "POST"][0]
    assert post[0] == f"/cluster/federations/{fid}/guests"
    assert post[1].get("json") == {"external_id": "carol@src-uuid"}


def test_guests_enroll_forwards_initial_tuples() -> None:
    """Enrollment forwards optional ReBAC seed tuples."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    fid = "22222222-2222-2222-2222-222222222222"
    _stage_guest_federation(client, fid=fid)
    client.expect(
        "POST",
        f"/cluster/federations/{fid}/guests",
        {"external_id": "dave@src", "realm": f"federation-{fid}", "offline_token": "T"},
    )

    tuples = [{"subject": "user:dave", "relation": "reader", "object": "dataset:x"}]
    FederationsAPI(client)["ORION"].guests.enroll("dave@src", initial_tuples=tuples)

    post = [kw for m, p, kw in client.calls if m == "POST"][0]
    assert post["json"]["initial_tuples"] == tuples


def test_guests_enroll_forwards_identity_proof() -> None:
    """Enrollment forwards an explicitly supplied out-of-band proof."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    fid = "44444444-4444-4444-4444-444444444444"
    _stage_guest_federation(client, fid=fid)
    client.expect(
        "POST",
        f"/cluster/federations/{fid}/guests",
        {"external_id": "eve@src", "realm": f"federation-{fid}", "offline_token": "T"},
    )

    proof = {
        "client_cert_pem": "-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----"
    }
    FederationsAPI(client)["ORION"].guests.enroll("eve@src", identity_proof=proof)

    post = [kw for m, p, kw in client.calls if m == "POST"][0]
    assert post["json"]["identity_proof"] == proof


def test_guests_enroll_omits_identity_proof_when_not_supplied() -> None:
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    fid = "55555555-5555-5555-5555-555555555555"
    _stage_guest_federation(client, fid=fid)
    client.expect(
        "POST",
        f"/cluster/federations/{fid}/guests",
        {
            "external_id": "frank@src",
            "realm": f"federation-{fid}",
            "offline_token": "T",
        },
    )

    FederationsAPI(client)["ORION"].guests.enroll("frank@src")

    post = [kw for m, p, kw in client.calls if m == "POST"][0]
    assert "identity_proof" not in post["json"]


def test_guests_revoke_posts_to_revoke_endpoint() -> None:
    """Revocation targets the receiver's guest revoke endpoint."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    fid = "33333333-3333-3333-3333-333333333333"
    _stage_guest_federation(client, fid=fid)
    client.expect(
        "POST",
        f"/cluster/federations/{fid}/guests/carol@src/revoke",
        {"success": True},
    )

    result = FederationsAPI(client)["ORION"].guests.revoke("carol@src")
    assert result == {"success": True}
    assert ("POST", f"/cluster/federations/{fid}/guests/carol@src/revoke") in [
        (m, p) for m, p, _ in client.calls
    ]
