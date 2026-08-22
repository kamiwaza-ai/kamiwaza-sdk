"""Two-peer, peer-proven PSK rotation assertions for live federation tests."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import KamiwazaError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RotationPair:
    initiator: KamiwazaClient
    receiver: KamiwazaClient
    initiator_id: str
    receiver_id: str


def _status(client: KamiwazaClient, federation_id: str) -> dict[str, Any]:
    return client.cluster.get_key_rotation_status(federation_id)


def _refusal(exc: KamiwazaError) -> tuple[int | None, str | None]:
    body = exc.body if isinstance(exc.body, dict) else {}
    detail = body.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    return exc.status_code, detail.get("reason")


def _row(client: KamiwazaClient, federation_id: str) -> dict[str, Any]:
    body = client._request("GET", f"/cluster/federations/{federation_id}")
    assert isinstance(body, dict), "federation read did not return an object"
    return body


def _assert_reachable(client: KamiwazaClient, federation_id: str) -> None:
    body = client._request("POST", f"/cluster/federations/{federation_id}/ping")
    assert isinstance(body, dict), "federation ping did not return an object"
    assert (
        body.get("reachable") is True
    ), f"federation {federation_id} was unreachable during rotation: {body!r}"


def assert_pair_reachable(pair: RotationPair) -> None:
    _assert_reachable(pair.initiator, pair.initiator_id)
    _assert_reachable(pair.receiver, pair.receiver_id)


def _settle_rotation(client: KamiwazaClient, federation_id: str) -> None:
    status = _status(client, federation_id)
    phase = status.get("phase")
    if phase == "STAGED":
        client.cluster.abort_key_rotation(
            federation_id,
            fingerprint=str(status["alternate_fingerprint"]),
            generation=str(status["generation"]),
        )
        return
    if phase == "ACTIVATING":
        client.cluster.activate_key_rotation(
            federation_id, fingerprint=str(status["alternate_fingerprint"])
        )
        status = _status(client, federation_id)
        phase = status.get("phase")
    if phase == "ACTIVE":
        client.cluster.complete_key_rotation(
            federation_id, fingerprint=str(status["active_fingerprint"])
        )


def settle_pair(pair: RotationPair) -> None:
    """Attempt both cleanups and surface the first failure."""
    first_error: Exception | None = None
    for client, federation_id in (
        (pair.initiator, pair.initiator_id),
        (pair.receiver, pair.receiver_id),
    ):
        try:
            _settle_rotation(client, federation_id)
        except Exception as exc:  # pragma: no cover - live failure recovery
            if first_error is None:
                first_error = exc
            else:
                logger.exception("second peer rotation cleanup also failed")
    if first_error is not None:
        raise first_error


def _assert_status(
    pair: RotationPair, phase: str, active: str, alternate: str | None
) -> None:
    for client, federation_id in (
        (pair.initiator, pair.initiator_id),
        (pair.receiver, pair.receiver_id),
    ):
        status = _status(client, federation_id)
        assert status.get("phase") == phase, status
        assert status.get("active_fingerprint") == active, status
        assert status.get("alternate_fingerprint") == alternate, status


def _assert_key_absent(pair: RotationPair, key: str) -> None:
    for client, federation_id in (
        (pair.initiator, pair.initiator_id),
        (pair.receiver, pair.receiver_id),
    ):
        public_payloads = (_row(client, federation_id), _status(client, federation_id))
        if any(key in json.dumps(payload) for payload in public_payloads):
            raise AssertionError(
                "a federation response exposed the staged plaintext PSK"
            )


def _recover_lost_stage(pair: RotationPair) -> None:
    pair.initiator.cluster.rotate_preshared_key(pair.initiator_id)
    lost = _status(pair.initiator, pair.initiator_id)
    fingerprint = str(lost["alternate_fingerprint"])
    generation = str(lost["generation"])
    with pytest.raises(KamiwazaError) as in_flight:
        pair.initiator.cluster.rotate_preshared_key(pair.initiator_id)
    assert _refusal(in_flight.value) == (409, "rotation_already_in_flight")
    aborted = pair.initiator.cluster.abort_key_rotation(
        pair.initiator_id, fingerprint=fingerprint, generation=generation
    )
    assert aborted.get("reason") == "rotation_aborted", aborted
    repeated = pair.initiator.cluster.abort_key_rotation(
        pair.initiator_id, fingerprint=fingerprint, generation=generation
    )
    assert repeated.get("reason") == "rotation_already_aborted", repeated


def _converge_divergent_stages(pair: RotationPair) -> tuple[str, str]:
    staged = pair.initiator.cluster.rotate_preshared_key(pair.initiator_id)
    divergent = pair.receiver.cluster.rotate_preshared_key(pair.receiver_id)
    fingerprint, key = str(staged["fingerprint"]), str(staged["preshared_key"])
    pair.receiver.cluster.abort_key_rotation(
        pair.receiver_id,
        fingerprint=str(divergent["fingerprint"]),
        generation=str(divergent["generation"]),
    )
    with pytest.raises(KamiwazaError) as mismatch:
        pair.receiver.cluster.adopt_preshared_key_rotation(
            pair.receiver_id, preshared_key=key, fingerprint="00" * 32
        )
    assert _refusal(mismatch.value) == (400, "rotation_fingerprint_mismatch")
    adopted = pair.receiver.cluster.adopt_preshared_key_rotation(
        pair.receiver_id, preshared_key=key, fingerprint=fingerprint
    )
    assert adopted.get("reason") == "rotation_staged", adopted
    repeated = pair.receiver.cluster.adopt_preshared_key_rotation(
        pair.receiver_id, preshared_key=key, fingerprint=fingerprint
    )
    assert repeated.get("reason") == "rotation_already_staged", repeated
    _assert_key_absent(pair, key)
    old = str(_status(pair.initiator, pair.initiator_id)["active_fingerprint"])
    return fingerprint, old


def _activate(pair: RotationPair, fingerprint: str, old: str) -> None:
    activated = pair.initiator.cluster.activate_key_rotation(
        pair.initiator_id, fingerprint=fingerprint
    )
    assert activated.get("reason") == "rotation_activated", activated
    repeated = pair.initiator.cluster.activate_key_rotation(
        pair.initiator_id, fingerprint=fingerprint
    )
    assert repeated.get("reason") == "rotation_already_activated", repeated
    _assert_status(pair, "ACTIVE", fingerprint, old)


def _complete(pair: RotationPair, fingerprint: str) -> None:
    closed = pair.initiator.cluster.complete_key_rotation(
        pair.initiator_id, fingerprint=fingerprint
    )
    assert closed.get("reason") == "rotation_closed", closed
    repeated = pair.initiator.cluster.complete_key_rotation(
        pair.initiator_id, fingerprint=fingerprint
    )
    assert repeated.get("reason") == "rotation_already_closed", repeated
    _assert_status(pair, "IDLE", fingerprint, None)


def exercise_peer_proven_rotation(pair: RotationPair) -> None:
    assert_pair_reachable(pair)
    _recover_lost_stage(pair)
    fingerprint, old = _converge_divergent_stages(pair)
    assert_pair_reachable(pair)
    _activate(pair, fingerprint, old)
    assert_pair_reachable(pair)
    _complete(pair, fingerprint)
    assert_pair_reachable(pair)
