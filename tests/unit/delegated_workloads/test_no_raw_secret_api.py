"""The delegated SDK has no public raw-secret credential surface."""

from __future__ import annotations

import inspect

import pytest

from kamiwaza_sdk.delegated_workloads._credential_modes import (
    UnsupportedCredentialMode,
    resolve_credential_mode,
)
from kamiwaza_sdk.delegated_workloads.api import DelegatedWorkloadAPI
from kamiwaza_sdk.delegated_workloads.client import DelegatedControlPlaneClient
from kamiwaza_sdk.delegated_workloads.executor import DelegatedExecutorClient
from kamiwaza_sdk.delegated_workloads.models import EffectReservationRequest

_PROHIBITED_IDENTIFIERS = ("raw_secret", "release_secret", "static_secret")


def test_public_sdk_methods_have_no_raw_release_entry_point() -> None:
    public_methods = {
        name
        for service in (
            DelegatedControlPlaneClient,
            DelegatedExecutorClient,
            DelegatedWorkloadAPI,
        )
        for name, value in inspect.getmembers(service, predicate=callable)
        if not name.startswith("_")
    }

    assert not any(
        prohibited in method
        for method in public_methods
        for prohibited in _PROHIBITED_IDENTIFIERS
    )
    assert "credential" not in EffectReservationRequest.model_fields
    assert "mode" not in EffectReservationRequest.model_fields


def test_request_schema_rejects_hidden_raw_release_fields() -> None:
    schema = EffectReservationRequest.model_json_schema()
    properties = schema["properties"]

    assert "credential_binding_id" in properties
    assert "credential" not in properties
    assert "secret" not in properties
    assert "mode" not in properties
    assert schema["additionalProperties"] is False


def test_unsupported_mode_fails_before_sdk_resolution_or_transport() -> None:
    resolved: list[str] = []

    for unsupported in ("raw_static_secret", object()):
        with pytest.raises(UnsupportedCredentialMode, match="^unsupported_mode$"):
            resolve_credential_mode(unsupported, resolved.append)

    assert resolved == []
    resolve_credential_mode("brokered", resolved.append)
    assert resolved == ["brokered"]
