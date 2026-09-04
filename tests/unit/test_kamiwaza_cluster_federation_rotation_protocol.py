"""ENG-10082 — SDK contract for peer-proven PSK rotation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pytest

from kamiwaza_sdk.exceptions import KamiwazaError

FEDERATION_ID = "11111111-2222-3333-4444-555555555555"
FINGERPRINT = "ab" * 32
GENERATION = "2026-08-22T12:34:56+00:00"


@dataclass(frozen=True)
class TransitionCase:
    method_name: str
    route: str
    kwargs: dict[str, str]
    payload: dict[str, str]


def _api(mock_client):
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    return ClusterAPI(client=mock_client)


def test_adopt_rotation_sends_the_oob_key_and_fingerprint(mock_client) -> None:
    expected = {
        "federation_id": FEDERATION_ID,
        "reason": "rotation_staged",
        "fingerprint": FINGERPRINT,
        "generation": GENERATION,
    }
    mock_client.expect(
        "POST",
        f"/cluster/federations/{FEDERATION_ID}/adopt-preshared-key-rotation",
        expected,
    )

    result = _api(mock_client).adopt_preshared_key_rotation(
        FEDERATION_ID,
        preshared_key="kzfed-operator-copy",
        fingerprint=FINGERPRINT,
    )

    assert result == expected
    assert mock_client.calls[0][2]["json"] == {
        "preshared_key": "kzfed-operator-copy",
        "fingerprint": FINGERPRINT,
    }


def test_rotation_status_is_a_read_without_key_material(mock_client) -> None:
    expected = {
        "reason": "rotation_status_returned",
        "phase": "STAGED",
        "active_fingerprint": "cd" * 32,
        "alternate_fingerprint": FINGERPRINT,
        "generation": GENERATION,
    }
    path = f"/cluster/federations/{FEDERATION_ID}/key-rotation-status"
    mock_client.expect("GET", path, expected)

    assert _api(mock_client).get_key_rotation_status(FEDERATION_ID) == expected
    assert mock_client.calls[0] == ("GET", path, {})


@pytest.mark.parametrize(
    "case",
    [
        TransitionCase(
            "activate_key_rotation",
            "activate-key-rotation",
            {"fingerprint": FINGERPRINT},
            {"fingerprint": FINGERPRINT},
        ),
        TransitionCase(
            "complete_key_rotation",
            "complete-key-rotation",
            {"fingerprint": FINGERPRINT},
            {"fingerprint": FINGERPRINT},
        ),
        TransitionCase(
            "abort_key_rotation",
            "abort-key-rotation",
            {"fingerprint": FINGERPRINT, "generation": GENERATION},
            {"fingerprint": FINGERPRINT, "generation": GENERATION},
        ),
    ],
)
def test_rotation_transition_sends_exact_cas_body(
    mock_client,
    case: TransitionCase,
) -> None:
    expected = {"federation_id": FEDERATION_ID, "reason": "ok"}
    mock_client.expect(
        "POST", f"/cluster/federations/{FEDERATION_ID}/{case.route}", expected
    )

    method: Callable[..., dict[str, Any]] = getattr(_api(mock_client), case.method_name)
    assert method(FEDERATION_ID, **case.kwargs) == expected
    assert mock_client.calls[0][2]["json"] == case.payload


def test_old_acknowledgement_cannot_claim_peer_proven_completion(mock_client) -> None:
    with pytest.raises(TypeError):
        _api(mock_client).complete_key_rotation(
            FEDERATION_ID, acknowledged=True  # type: ignore[call-arg]
        )

    assert mock_client.calls == []


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda api: api.adopt_preshared_key_rotation(
                FEDERATION_ID,
                preshared_key="kzfed-operator-copy",
                fingerprint=FINGERPRINT,
            ),
            id="adopt",
        ),
        pytest.param(
            lambda api: api.get_key_rotation_status(FEDERATION_ID), id="status"
        ),
        pytest.param(
            lambda api: api.activate_key_rotation(
                FEDERATION_ID, fingerprint=FINGERPRINT
            ),
            id="activate",
        ),
        pytest.param(
            lambda api: api.complete_key_rotation(
                FEDERATION_ID, fingerprint=FINGERPRINT
            ),
            id="complete",
        ),
        pytest.param(
            lambda api: api.abort_key_rotation(
                FEDERATION_ID,
                fingerprint=FINGERPRINT,
                generation=GENERATION,
            ),
            id="abort",
        ),
    ],
)
def test_rotation_refusals_propagate_without_fallback(mock_client, call) -> None:
    refusal = KamiwazaError(
        "refused",
        status_code=409,
        body={"detail": {"reason": "rotation_state_conflict"}},
    )
    for method, route in (
        ("POST", "adopt-preshared-key-rotation"),
        ("GET", "key-rotation-status"),
        ("POST", "activate-key-rotation"),
        ("POST", "complete-key-rotation"),
        ("POST", "abort-key-rotation"),
    ):
        mock_client.raise_on(
            method, f"/cluster/federations/{FEDERATION_ID}/{route}", refusal
        )

    with pytest.raises(KamiwazaError) as exc_info:
        call(_api(mock_client))

    assert exc_info.value is refusal
