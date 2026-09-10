"""Recorded no-secret contract for delegated credential broker calls."""

from __future__ import annotations

import json
import pickle
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import jwt
import pytest
from pydantic import ValidationError

from kamiwaza_sdk.delegated_workloads import (
    CredentialBindingSummary,
    CredentialBroker,
    CredentialOperationParameters,
    CredentialUseRequest,
    CredentialUseStatus,
    DelegatedProtocolError,
    DelegatedWorkloadTransport,
    DPoPNonceRequired,
    EffectReservation,
    TrustedAdapterLease,
)
from kamiwaza_sdk.delegated_workloads.transport import SessionPort

from .protocol_test_support import (
    CORRELATION_ID,
    DIGEST,
    EFFECT_ID,
    StubResponse,
    StubSession,
)

pytestmark = pytest.mark.contract
NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)
BASE_URL = "https://core.example.test/api/v1/delegated-workloads"
BINDING_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
LEASE_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
CLIENT_ID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
BROKER_HANDLE = "opaque.broker-handle.signature"
EFFECT_CAPABILITY = "header.effect-capability.signature"


def _too_deep_parameters() -> dict[str, object]:
    root: dict[str, object] = {}
    cursor = root
    for index in range(9):
        nested: dict[str, object] = {}
        cursor[f"level_{index}"] = nested
        cursor = nested
    return root


def test_member_binding_discovery_returns_only_typed_safe_metadata() -> None:
    session = StubSession([StubResponse(200, [_binding_payload()])])
    broker = _broker(session, member_session=session)

    bindings = broker.list_bindings(
        UUID(CLIENT_ID), operation="google.drive.files.list"
    )

    assert len(bindings) == 1
    assert bindings[0].id == UUID(BINDING_ID)
    assert bindings[0].mode.value == "brokered"
    assert bindings[0].allowed_operations == ("google.drive.files.list",)
    assert session.calls[0] == (
        "GET",
        f"{BASE_URL}/credential-bindings",
        {
            "params": {
                "client_id": CLIENT_ID,
                "operation": "google.drive.files.list",
            }
        },
    )
    assert "connection" not in type(bindings[0]).model_fields
    assert "scope" not in type(bindings[0]).model_fields


def test_member_binding_discovery_requires_a_session_and_list_response() -> None:
    session = StubSession([StubResponse(200, {"items": []})])

    with pytest.raises(ValueError, match="member session is unavailable"):
        _broker(session).list_bindings(UUID(CLIENT_ID))
    with pytest.raises(DelegatedProtocolError):
        _broker(session, member_session=session).list_bindings(UUID(CLIENT_ID))

    assert session.calls[0][2]["params"] == {"client_id": CLIENT_ID}


@pytest.mark.parametrize("operations", [[], ["google.drive.files.list"] * 2])
def test_binding_discovery_rejects_missing_or_duplicate_operations(
    operations: list[str],
) -> None:
    payload = _binding_payload()
    payload["allowed_operations"] = operations

    with pytest.raises(ValidationError, match="credential operations are invalid"):
        CredentialBindingSummary.model_validate(payload)


@pytest.mark.parametrize("status", list(CredentialUseStatus))
def test_broker_executes_once_and_returns_a_typed_terminal_receipt(
    status: CredentialUseStatus,
) -> None:
    session = StubSession([StubResponse(200, _receipt_payload(status.value))])
    broker = _broker(session)

    receipt = broker.execute(_lease())

    assert receipt.status is status
    assert receipt.lease_id == UUID(LEASE_ID)
    assert receipt.result == {"items": [{"id": "doc-7"}]}
    assert receipt.correlation_id == UUID(CORRELATION_ID)
    assert len(session.calls) == 1
    _assert_broker_request(session.calls[0])


def test_broker_use_never_replays_after_a_nonce_challenge() -> None:
    session = StubSession(
        [
            StubResponse(
                401,
                {
                    "error": {
                        "code": "dpop_nonce_required",
                        "message": "safe nonce challenge",
                        "retry_classification": "nonce_required",
                        "correlation_id": CORRELATION_ID,
                        "safe_details": {},
                    }
                },
                {"DPoP-Nonce": "core-issued-nonce-0123456789"},
            )
        ]
    )

    with pytest.raises(DPoPNonceRequired):
        _broker(session).execute(_lease())

    assert len(session.calls) == 1


def test_trusted_adapter_lease_redacts_and_rejects_serialization() -> None:
    lease = _lease()

    assert BROKER_HANDLE not in repr(lease)
    assert EFFECT_CAPABILITY not in repr(lease)
    assert BROKER_HANDLE not in str(lease)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(lease)
    with pytest.raises(TypeError, match="cannot be serialized"):
        lease.__reduce__()
    with pytest.raises(TypeError):
        json.dumps(lease)
    assert not hasattr(lease, "access_token")
    assert not hasattr(lease, "refresh_token")
    assert not hasattr(lease, "raw_token")


@pytest.mark.parametrize(
    "parameters",
    [
        {"body": {"access_token": "must-not-cross"}},
        {"params": {"password": "must-not-cross"}},
        {"body": {"nested": {"client-secret": "must-not-cross"}}},
        {"unknown": {}},
    ],
)
def test_credential_parameters_reject_secret_or_unknown_fields_locally(
    parameters: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        CredentialOperationParameters.model_validate(parameters)


@pytest.mark.parametrize(
    "parameters",
    [
        {"params": []},
        {"params": {1: "non-string-key"}},
        {"params": {"k" * 257: "too-long-key"}},
        {"params": {"number": float("nan")}},
        {"params": {"text": "x" * 65_537}},
        {"params": {"value": object()}},
        {"params": {"values": [0] * 4096}},
        {"params": _too_deep_parameters()},
    ],
)
def test_credential_parameters_enforce_the_local_json_bounds(
    parameters: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="credential parameters are invalid"):
        CredentialOperationParameters.model_validate(parameters)


def test_credential_parameters_accept_all_safe_json_scalar_types() -> None:
    assert CredentialOperationParameters().params is None
    assert CredentialOperationParameters.model_validate({"params": None}).params is None
    parameters = CredentialOperationParameters(
        params={
            "absent": None,
            "enabled": True,
            "number": 7,
            "ratio": 0.5,
            "name": "safe",
            "items": [False, 3],
        }
    )

    assert parameters.params is not None
    assert parameters.params["name"] == "safe"


def test_lease_requires_both_opaque_authorities_from_an_allowed_effect() -> None:
    request = _request()

    for field in ("broker_handle", "effect_capability"):
        payload = _effect_payload()
        payload[field] = None
        effect = EffectReservation.model_validate(payload)
        with pytest.raises(ValueError, match="broker authority is unavailable"):
            TrustedAdapterLease.from_effect(effect, request)

    denied = _effect_payload()
    denied.update({"decision": "deny", "status": "denied"})
    with pytest.raises(ValueError, match="broker authority is unavailable"):
        TrustedAdapterLease.from_effect(
            EffectReservation.model_validate(denied), request
        )


def _broker(
    session: StubSession,
    *,
    member_session: StubSession | None = None,
) -> CredentialBroker:
    transport = DelegatedWorkloadTransport(
        cast(SessionPort, session), clock=lambda: NOW
    )
    return CredentialBroker(BASE_URL, transport, member_session=member_session)


def _lease() -> TrustedAdapterLease:
    effect = EffectReservation.model_validate(_effect_payload())
    return TrustedAdapterLease.from_effect(effect, _request())


def _request() -> CredentialUseRequest:
    return CredentialUseRequest(
        credential_binding_id=UUID(BINDING_ID),
        operation_id="google.drive.files.list",
        request_digest=DIGEST,
        parameters=CredentialOperationParameters(
            params={"page_size": 20},
            resource_id="folder-7",
        ),
    )


def _effect_payload() -> dict[str, object]:
    return {
        "effect_id": EFFECT_ID,
        "decision": "allow",
        "status": "reserved",
        "policy_version": "policy-v1",
        "reason_codes": ["allowed"],
        "effect_capability": EFFECT_CAPABILITY,
        "broker_handle": BROKER_HANDLE,
        "valid_until": "2026-08-09T12:01:00Z",
        "correlation_id": CORRELATION_ID,
    }


def _binding_payload() -> dict[str, object]:
    return {
        "id": BINDING_ID,
        "provider": "google",
        "display_name": "Google",
        "allowed_operations": ["google.drive.files.list"],
        "mode": "brokered",
        "status": "active",
        "revocation_supported": True,
        "maximum_ephemeral_ttl_seconds": None,
    }


def _receipt_payload(status: str) -> dict[str, object]:
    return {
        "lease_id": LEASE_ID,
        "status": status,
        "result": {"items": [{"id": "doc-7"}]},
        "correlation_id": CORRELATION_ID,
    }


def _assert_broker_request(
    call: tuple[str, str, dict[str, object]],
) -> None:
    assert call[0:2] == ("POST", f"{BASE_URL}/credential-uses")
    headers = call[2]["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == f"DPoP {EFFECT_CAPABILITY}"
    proof = jwt.decode(headers["DPoP"], options={"verify_signature": False})
    assert "ath" in proof
    body = json.loads(cast(bytes, call[2]["data"]))
    assert body == {
        "broker_handle": BROKER_HANDLE,
        "credential_binding_id": BINDING_ID,
        "effect_id": EFFECT_ID,
        "operation_id": "google.drive.files.list",
        "parameters": {
            "params": {"page_size": 20},
            "resource_id": "folder-7",
        },
        "request_digest": DIGEST,
    }
