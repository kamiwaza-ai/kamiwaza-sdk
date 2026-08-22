"""ENG-9807 — adjacent federation trust methods on the canonical surface.

Covers the stage, peer-CA, and reconnect methods on
``kamiwaza_sdk.services.cluster_federation.ClusterAPI``:

    kz.cluster.rotate_preshared_key(id)
        -> dict  (POST /cluster/federations/{id}/rotate-preshared-key)
    kz.cluster.refresh_peer_ca(id, *, ca_pem, acknowledged_fingerprint)
        -> dict  (POST /cluster/federations/{id}/refresh-peer-ca)
    kz.cluster.reconnect_federation(id)
        -> dict  (POST /cluster/federations/{id}/reconnect)

The peer-proven activation/completion protocol has its own contract file. The
body assertion here protects the CA acknowledgement from being silently
dropped by the SDK.
"""

from __future__ import annotations

import uuid

import pytest

FEDERATION_ID = "11111111-2222-3333-4444-555555555555"


def _api(mock_client):
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    return ClusterAPI(client=mock_client)


def test_rotate_preshared_key_posts_and_returns_the_new_key(mock_client) -> None:
    payload = {
        "federation_id": FEDERATION_ID,
        "reason": "rotation_staged",
        "rotated_at": 1785000000,
        "generation": "2026-08-22T12:34:56+00:00",
        "fingerprint": "ab" * 32,
        "preshared_key": "kzfed-abc123",
    }
    mock_client.expect(
        "POST", f"/cluster/federations/{FEDERATION_ID}/rotate-preshared-key", payload
    )

    result = _api(mock_client).rotate_preshared_key(FEDERATION_ID)

    assert result == payload
    method, path, kwargs = mock_client.calls[0]
    assert (method, path) == (
        "POST",
        f"/cluster/federations/{FEDERATION_ID}/rotate-preshared-key",
    )
    # Opening a window is additive and takes no acknowledgement — a body here
    # would mean the caller is being asked to confirm something.
    assert "json" not in kwargs


def test_rotate_preshared_key_accepts_a_uuid_object(mock_client) -> None:
    """Federation ids arrive as ``UUID`` from ``federations.list()``."""
    federation_id = uuid.UUID(FEDERATION_ID)
    mock_client.expect(
        "POST",
        f"/cluster/federations/{FEDERATION_ID}/rotate-preshared-key",
        {"reason": "rotation_staged"},
    )

    _api(mock_client).rotate_preshared_key(federation_id)

    _method, path, _kwargs = mock_client.calls[0]
    assert path == f"/cluster/federations/{FEDERATION_ID}/rotate-preshared-key"


def test_refresh_peer_ca_sends_both_halves(mock_client) -> None:
    """Both the CA and the fingerprint the operator acknowledged must ship.

    Dropping ``acknowledged_fingerprint`` degrades the endpoint into the
    accept-whatever-is-presented button the acknowledgement exists to prevent;
    the server would refuse with 400, but only because the field was missing.
    """
    ca_pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    fingerprint = "9f" * 32
    mock_client.expect(
        "POST",
        f"/cluster/federations/{FEDERATION_ID}/refresh-peer-ca",
        {
            "federation_id": FEDERATION_ID,
            "fingerprint": fingerprint,
            "previous_fingerprint": "ab" * 32,
        },
    )

    result = _api(mock_client).refresh_peer_ca(
        FEDERATION_ID, ca_pem=ca_pem, acknowledged_fingerprint=fingerprint
    )

    assert result["fingerprint"] == fingerprint
    assert result["previous_fingerprint"] == "ab" * 32
    _method, _path, kwargs = mock_client.calls[0]
    assert kwargs.get("json") == {
        "ca_pem": ca_pem,
        "acknowledged_fingerprint": fingerprint,
    }


def test_refresh_peer_ca_does_not_normalise_the_pem(mock_client) -> None:
    """The PEM ships byte-for-byte.

    The server fingerprints the whitespace-normalised text, so client-side
    tidying would not change the fingerprint — but it WOULD change what gets
    stored and later presented to TLS verification.
    """
    ca_pem = "  -----BEGIN CERTIFICATE-----\r\nMIIB\r\n-----END CERTIFICATE-----  "
    mock_client.expect(
        "POST", f"/cluster/federations/{FEDERATION_ID}/refresh-peer-ca", {}
    )

    _api(mock_client).refresh_peer_ca(
        FEDERATION_ID, ca_pem=ca_pem, acknowledged_fingerprint="cd" * 32
    )

    _method, _path, kwargs = mock_client.calls[0]
    assert kwargs.get("json", {})["ca_pem"] == ca_pem


def test_reconnect_federation_posts_to_the_reconnect_route(mock_client) -> None:
    payload = {
        "federation_id": FEDERATION_ID,
        "restored": 3,
        "peer_idp_enabled": True,
        "guests_enabled": 3,
    }
    mock_client.expect(
        "POST", f"/cluster/federations/{FEDERATION_ID}/reconnect", payload
    )

    result = _api(mock_client).reconnect_federation(FEDERATION_ID)

    assert result == payload
    method, path, kwargs = mock_client.calls[0]
    assert (method, path) == ("POST", f"/cluster/federations/{FEDERATION_ID}/reconnect")
    assert "json" not in kwargs


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda api: api.rotate_preshared_key(FEDERATION_ID), id="rotate"),
        pytest.param(
            lambda api: api.complete_key_rotation(FEDERATION_ID, fingerprint="ef" * 32),
            id="complete",
        ),
        pytest.param(
            lambda api: api.refresh_peer_ca(
                FEDERATION_ID, ca_pem="pem", acknowledged_fingerprint="ef" * 32
            ),
            id="refresh_peer_ca",
        ),
        pytest.param(
            lambda api: api.reconnect_federation(FEDERATION_ID), id="reconnect"
        ),
    ],
)
def test_server_refusals_propagate_untouched(mock_client, call) -> None:
    """Every refusal is the server's to make — none of these are swallowed.

    A rotation refused mid-flight, a fingerprint that does not match, a
    reconnect on a live federation: each one means the operator's model of the
    trust state is wrong, which is exactly the thing that must not be retried
    away inside the client.
    """
    from kamiwaza_sdk.exceptions import KamiwazaError

    for path in (
        "rotate-preshared-key",
        "complete-key-rotation",
        "refresh-peer-ca",
        "reconnect",
    ):
        mock_client.raise_on(
            "POST",
            f"/cluster/federations/{FEDERATION_ID}/{path}",
            KamiwazaError(
                "refused",
                status_code=409,
                body={"detail": {"reason": "refused_by_server"}},
            ),
        )

    with pytest.raises(KamiwazaError) as exc_info:
        call(_api(mock_client))

    assert exc_info.value.status_code == 409
