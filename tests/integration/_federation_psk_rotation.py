"""Two-peer PSK rotation, Catalog cleanup, and stale-key live assertions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, KamiwazaError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RotationPair:
    initiator: KamiwazaClient
    receiver: KamiwazaClient
    initiator_id: str
    receiver_id: str
    initial_psk: str = field(repr=False)


@dataclass(frozen=True)
class _TransitionSpec:
    method_name: str
    success_reason: str
    repeated_reason: str
    settled_phase: str


_ACTIVATION = _TransitionSpec(
    "activate_key_rotation",
    "rotation_activated",
    "rotation_already_activated",
    "ACTIVE",
)
_COMPLETION = _TransitionSpec(
    "complete_key_rotation",
    "rotation_closed",
    "rotation_already_closed",
    "IDLE",
)

_CATALOG_POLL_ATTEMPTS = 15
_CATALOG_POLL_DELAY_SECONDS = 2
_PING_BODY = b"{}"


@dataclass(frozen=True)
class _SecretIdentity:
    urn: str
    name: str


@dataclass(frozen=True)
class _PairSecretInventory:
    initiator: frozenset[_SecretIdentity]
    receiver: frozenset[_SecretIdentity]


@dataclass(frozen=True)
class _PingOutcome:
    status_code: int
    detail: str | None


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


def _rotation_secrets(
    client: KamiwazaClient, federation_id: str
) -> frozenset[_SecretIdentity]:
    prefix = f"clusterfed_{federation_id}"
    secrets = client.catalog.list_secrets(query=prefix)
    return frozenset(
        _SecretIdentity(str(item.urn), str(item.name))
        for item in secrets
        if item.name == prefix or item.name.startswith(f"{prefix}_rot")
    )


def _wait_for_rotation_secrets(
    client: KamiwazaClient,
    federation_id: str,
    predicate: Callable[[frozenset[_SecretIdentity]], bool],
    expectation: str,
) -> frozenset[_SecretIdentity]:
    for attempt in range(_CATALOG_POLL_ATTEMPTS):
        found = _rotation_secrets(client, federation_id)
        if predicate(found):
            return found
        if attempt < _CATALOG_POLL_ATTEMPTS - 1:
            time.sleep(_CATALOG_POLL_DELAY_SECONDS)
    raise AssertionError(
        f"Catalog did not converge to {expectation}: observed_count={len(found)}"
    )


def _initial_pair_secret_inventory(pair: RotationPair) -> _PairSecretInventory:
    return _PairSecretInventory(
        initiator=_wait_for_rotation_secrets(
            pair.initiator,
            pair.initiator_id,
            lambda found: len(found) == 1,
            "one initial K1 identity",
        ),
        receiver=_wait_for_rotation_secrets(
            pair.receiver,
            pair.receiver_id,
            lambda found: len(found) == 1,
            "one initial K1 identity",
        ),
    )


def _staged_inventory_predicate(
    initial: frozenset[_SecretIdentity], fingerprint: str
) -> Callable[[frozenset[_SecretIdentity]], bool]:
    fingerprint_prefix = fingerprint[:12]

    def _matches(found: frozenset[_SecretIdentity]) -> bool:
        successor = found - initial
        return (
            len(found) == 2
            and initial <= found
            and len(successor) == 1
            and next(iter(successor)).name.endswith(fingerprint_prefix)
        )

    return _matches


def _staged_pair_secret_inventory(
    pair: RotationPair, initial: _PairSecretInventory, fingerprint: str
) -> _PairSecretInventory:
    return _PairSecretInventory(
        initiator=_wait_for_rotation_secrets(
            pair.initiator,
            pair.initiator_id,
            _staged_inventory_predicate(initial.initiator, fingerprint),
            "the initial K1 plus one fingerprint-bound K2 identity",
        ),
        receiver=_wait_for_rotation_secrets(
            pair.receiver,
            pair.receiver_id,
            _staged_inventory_predicate(initial.receiver, fingerprint),
            "the initial K1 plus one fingerprint-bound K2 identity",
        ),
    )


def _closed_pair_secret_inventory(
    pair: RotationPair,
    initial: _PairSecretInventory,
    staged: _PairSecretInventory,
) -> _PairSecretInventory:
    initiator_successor = staged.initiator - initial.initiator
    receiver_successor = staged.receiver - initial.receiver
    return _PairSecretInventory(
        initiator=_wait_for_rotation_secrets(
            pair.initiator,
            pair.initiator_id,
            lambda found: found == initiator_successor,
            "the exact staged successor identity on the initiator",
        ),
        receiver=_wait_for_rotation_secrets(
            pair.receiver,
            pair.receiver_id,
            lambda found: found == receiver_successor,
            "the exact staged successor identity on the receiver",
        ),
    )


def _assert_secret_retirement(
    pair: RotationPair,
    initial: _PairSecretInventory,
    staged: _PairSecretInventory,
    closed: _PairSecretInventory,
) -> None:
    for old, transition, settled in (
        (initial.initiator, staged.initiator, closed.initiator),
        (initial.receiver, staged.receiver, closed.receiver),
    ):
        assert old <= transition, "K1 disappeared before the dual-key window closed"
        assert (
            settled == transition - old
        ), "the Catalog successor identity changed across rotation completion"
    _wait_for_retired_secrets_absent(pair, initial)


def _wait_for_retired_secrets_absent(
    pair: RotationPair, initial: _PairSecretInventory
) -> None:
    targets = tuple(
        (client, secret.urn)
        for client, secrets in (
            (pair.initiator, initial.initiator),
            (pair.receiver, initial.receiver),
        )
        for secret in secrets
    )
    remaining = targets
    for attempt in range(_CATALOG_POLL_ATTEMPTS):
        remaining = tuple(
            target for target in remaining if not _secret_is_absent(*target)
        )
        if not remaining:
            return
        if attempt < _CATALOG_POLL_ATTEMPTS - 1:
            time.sleep(_CATALOG_POLL_DELAY_SECONDS)
    raise AssertionError(
        "the retired K1 secret remains directly readable in Catalog: "
        f"affected_peers={len(remaining)}"
    )


def _secret_is_absent(client: KamiwazaClient, secret_urn: str) -> bool:
    try:
        client.catalog.secrets.get(secret_urn)
    except APIError as exc:
        if exc.status_code == 404:
            return True
        raise
    return False


def _bound_ping_signature(psk: str, cluster_id: str, issued_at: int) -> str:
    body_hash = hashlib.sha256(_PING_BODY).hexdigest()
    payload = (
        f"body_sha256={body_hash}\n"
        f"cluster_id={cluster_id}\n"
        "method=POST\n"
        "endpoint=ping\n"
        f"iat={issued_at}"
    ).encode()
    digest = hmac.new(psk.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _key_ping_outcome(
    client: KamiwazaClient, federation_id: str, candidate_psk: str
) -> _PingOutcome:
    source_cluster_id = str(_row(client, federation_id).get("remote_cluster_id") or "")
    assert source_cluster_id, "federation row has no peer cluster identity"
    issued_at = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-Cluster-Signature": _bound_ping_signature(
            candidate_psk, source_cluster_id, issued_at
        ),
        "X-Cluster-Issued-At": str(issued_at),
        "X-Cluster-Id": source_cluster_id,
    }
    response = client.session.post(
        f"{client.base_url}/cluster/remote/ping",
        data=_PING_BODY,
        headers=headers,
        timeout=10,
    )
    detail = None
    if response.status_code != 200:
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and isinstance(body.get("detail"), str):
            detail = body["detail"]
    return _PingOutcome(int(response.status_code), detail)


def _initial_key_outcomes(pair: RotationPair) -> tuple[_PingOutcome, _PingOutcome]:
    return (
        _key_ping_outcome(pair.initiator, pair.initiator_id, pair.initial_psk),
        _key_ping_outcome(pair.receiver, pair.receiver_id, pair.initial_psk),
    )


def _assert_initial_key_accepted(pair: RotationPair) -> None:
    statuses = tuple(outcome.status_code for outcome in _initial_key_outcomes(pair))
    assert statuses == (200, 200), f"K1 stopped working before retirement: {statuses}"


def _assert_retired_key_refused(pair: RotationPair) -> None:
    outcomes = _initial_key_outcomes(pair)
    expected = _PingOutcome(403, "Invalid cluster signature")
    assert outcomes == (expected, expected), (
        "retired K1 was not refused by signature validation; " f"outcomes={outcomes}"
    )


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


def _apply_idempotent_transition(
    pair: RotationPair,
    fingerprint: str,
    alternate: str | None,
    spec: _TransitionSpec,
) -> None:
    transition = getattr(pair.initiator.cluster, spec.method_name)
    response = transition(pair.initiator_id, fingerprint=fingerprint)
    assert response.get("reason") == spec.success_reason, response
    repeated = transition(pair.initiator_id, fingerprint=fingerprint)
    assert repeated.get("reason") == spec.repeated_reason, repeated
    _assert_status(pair, spec.settled_phase, fingerprint, alternate)


def _activate(pair: RotationPair, fingerprint: str, old: str) -> None:
    _apply_idempotent_transition(pair, fingerprint, old, _ACTIVATION)


def _complete(pair: RotationPair, fingerprint: str) -> None:
    _apply_idempotent_transition(pair, fingerprint, None, _COMPLETION)


def exercise_peer_proven_rotation(pair: RotationPair) -> None:
    initial_secrets = _initial_pair_secret_inventory(pair)
    assert_pair_reachable(pair)
    _recover_lost_stage(pair)
    fingerprint, old = _converge_divergent_stages(pair)
    staged_secrets = _staged_pair_secret_inventory(pair, initial_secrets, fingerprint)
    _assert_initial_key_accepted(pair)
    assert_pair_reachable(pair)
    _activate(pair, fingerprint, old)
    _assert_initial_key_accepted(pair)
    assert_pair_reachable(pair)
    _complete(pair, fingerprint)
    closed_secrets = _closed_pair_secret_inventory(
        pair, initial_secrets, staged_secrets
    )
    _assert_secret_retirement(pair, initial_secrets, staged_secrets, closed_secrets)
    _assert_retired_key_refused(pair)
    assert_pair_reachable(pair)
