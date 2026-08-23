"""Executable compatibility ceiling for the delegated-workload v1 SDK."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from kamiwaza_sdk.delegated_workloads import (
    DestinationRef,
    EffectAuthorization,
    EffectAuthorizationDecision,
    EffectConsumption,
    EffectDetail,
    EffectReservationRequest,
    ResourceGuardRegistration,
    RunDetail,
    RunTransition,
)

from .protocol_test_support import (
    DIGEST,
    authorization_payload,
    consumption_payload,
    effect_detail_payload,
    run_detail_payload,
)


pytestmark = pytest.mark.contract
_ROOT = Path(__file__).parents[3]
_FIXTURE_PATH = _ROOT / "docs/delegated-workloads/conformance-v1.json"
_REVISION_ID = UUID("bbbbbbbb-cccc-4ddd-8eee-ffffffffffff")
_ResponseCase = tuple[type[BaseModel], Callable[[], dict[str, object]]]
_RESPONSE_CASES: tuple[_ResponseCase, ...] = (
    (RunDetail, run_detail_payload),
    (EffectDetail, effect_detail_payload),
    (EffectAuthorization, authorization_payload),
    (EffectConsumption, consumption_payload),
)


@pytest.mark.parametrize(("model", "payload_factory"), _RESPONSE_CASES)
def test_optional_response_fields_are_additive(
    model: type[BaseModel],
    payload_factory: Callable[[], dict[str, object]],
) -> None:
    payload = payload_factory()
    payload["future_optional_observation"] = {"state": "available"}

    response = model.model_validate(payload)

    assert response.model_extra == {
        "future_optional_observation": {"state": "available"}
    }
    assert response.model_dump()["future_optional_observation"] == {
        "state": "available"
    }


def test_v1_claim_meanings_remain_closed() -> None:
    assert [value.value for value in EffectAuthorizationDecision] == [
        "allow",
        "deny",
    ]
    assert [value.value for value in RunTransition] == [
        "start",
        "heartbeat",
        "acknowledge_cancel",
        "succeed",
        "fail",
        "cancel",
        "ambiguous",
    ]
    with pytest.raises(ValueError):
        EffectAuthorizationDecision("pending_approval")


def test_v1_requests_cannot_silently_add_required_authority() -> None:
    payload = _reservation_payload()
    assert EffectReservationRequest.model_validate(payload).audience == "resource"

    with pytest.raises(ValidationError):
        EffectReservationRequest.model_validate(payload | {"future_scope": "write"})
    payload.pop("audience")
    with pytest.raises(ValidationError):
        EffectReservationRequest.model_validate(payload)


def test_v1_default_authority_remains_https_only() -> None:
    destination = DestinationRef(host="resource.example.test", port=443)

    assert destination.scheme == "https"
    with pytest.raises(ValidationError):
        DestinationRef.model_validate(
            {"host": "resource.example.test", "port": 80, "scheme": "http"}
        )


@pytest.mark.parametrize(
    "change",
    [
        {"audience": "http://resource.example.test"},
        {"action": ""},
        {"descriptor_version": "1"},
        {"guard_contract_version": "v1"},
    ],
)
def test_v1_guard_checks_cannot_be_relaxed_in_place(
    change: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ResourceGuardRegistration(**(_registration_values() | change))


def test_published_policy_requires_a_new_version_for_authority_changes() -> None:
    compatibility = _compatibility()

    assert compatibility["current_protocol"] == "v1"
    assert compatibility["supported_protocols"] == ["v1"]
    assert compatibility["within_v1_changes"] == {
        "add_optional_response_field": "compatible",
        "change_claim_meaning": "requires_new_version",
        "relax_guard_check": "requires_new_version",
        "add_required_field": "requires_new_version",
        "widen_default_authority": "requires_new_version",
    }
    assert compatibility["unknown_version_behavior"] == "reject_before_execution"


def _reservation_payload() -> dict[str, object]:
    return {
        "effect_key": "document:read",
        "effect_digest": DIGEST,
        "action": "read",
        "resource": {
            "type": "example.document",
            "descriptor_version": "v1",
            "id": "document:7",
        },
        "audience": "resource",
    }


def _registration_values() -> dict[str, Any]:
    return {
        "resource_type": "example.document",
        "descriptor_version": "v1",
        "revision_id": _REVISION_ID,
        "audience": "https://resource.example.test",
        "action": "read",
        "guard_contract_version": "guard:v1",
    }


def _compatibility() -> dict[str, Any]:
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    compatibility = fixture["compatibility"]
    assert isinstance(compatibility, dict)
    return compatibility
