"""A declared workload identity must reach the platform, or fail loudly.

The declaration is the only thing that causes a workload to be registered. If
publishing drops it, the extension deploys and looks healthy while its
unattended work is silently unauthorized — the same failure as declaring
nothing at all. These tests pin forwarding and rejection rather than the
declaration's shape, which the manifest schema test already covers.
"""

from __future__ import annotations

from typing import Any

import pytest

from kamiwaza_extensions.catalog_overlay import build_overlay_entry
from kamiwaza_extensions.commands.dev import _build_patch_kwargs
from kamiwaza_extensions.connections import ConnectionInfo
from kamiwaza_extensions.payload_builder import PayloadBuilder
from kamiwaza_extensions.validators.workload_identity import (
    WorkloadIdentityDeclarationError,
    declaration_errors,
)

_COMPOSE: dict[str, Any] = {"services": {"api-backend": {"image": "example:1"}}}


def _declaration() -> dict[str, Any]:
    """A declaration shaped exactly as the shipped schema requires."""
    return {
        "contract_versions": ["v1"],
        "required_capabilities": [
            "automation_grants",
            "immutable_workload_revisions",
            "resource_registration",
            "atomic_queue_claims",
            "run_capabilities",
            "run_lifecycle",
            "effect_capabilities",
            "effect_lifecycle",
            "dpop",
            "durable_revocation",
            "durable_audit",
            "dual_principal_rebac",
            "model_attribution",
            "member_workload_quota",
            "brokered_credentials",
            "exact_effect_approval",
            "registrar_registration",
            "workload_attestation",
            "platform_consent",
            "protected_resource_guard",
        ],
        "roles": [
            {
                "id": "task-control",
                "type": "control_plane",
                "service": "api-backend",
                "attestation": {
                    "profiles": [
                        "kubernetes-tokenreview-v1",
                        "kubernetes-offline-v1",
                    ],
                    "audience": "kamiwaza-workload-sts",
                    "ttl_seconds": 600,
                },
                "platform_operations": [
                    "intent:create",
                    "intent:read",
                    "run:reserve",
                    "readiness:read",
                ],
            }
        ],
    }


def _entry(metadata: dict[str, Any]) -> dict[str, Any]:
    return build_overlay_entry(
        version="0.1.0",
        transformed_compose=dict(_COMPOSE),
        canonical_refs={},
        git_sha="abc1234",
        git_branch="main",
        dirty=False,
        metadata=metadata,
    )


def test_declaration_reaches_the_published_entry() -> None:
    entry = _entry({"workload_identity": _declaration()})

    assert entry["workload_identity"] == _declaration()


def test_absent_declaration_is_not_invented() -> None:
    entry = _entry({"display_name": "Example"})

    assert "workload_identity" not in entry


def test_malformed_declaration_fails_the_publish(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dropping it would deploy an extension whose workload is never registered."""
    broken = _declaration()
    broken["roles"][0].pop("attestation")

    with pytest.raises(WorkloadIdentityDeclarationError) as failure:
        _entry({"workload_identity": broken})

    assert "attestation" in str(failure.value)


def test_unknown_capability_family_is_rejected() -> None:
    broken = _declaration()
    broken["required_capabilities"].append("invented_family")

    with pytest.raises(WorkloadIdentityDeclarationError):
        _entry({"workload_identity": broken})


def test_declaration_errors_names_the_offending_field() -> None:
    broken = _declaration()
    broken["contract_versions"] = []

    errors = declaration_errors(broken)

    assert errors and any("contract_versions" in message for message in errors)


def _direct_payload(metadata: dict[str, Any]):
    return PayloadBuilder().build(
        metadata={"name": "example", "version": "0.1.0", **metadata},
        transformed_compose=dict(_COMPOSE),
        connection=ConnectionInfo(
            name="test",
            url="https://cluster.test/api",
            active=True,
            created_at=0.0,
        ),
        dev_name="example-dev-abc123",
    )


def test_declaration_reaches_create_and_patch_payloads() -> None:
    payload = _direct_payload({"workload_identity": _declaration()})

    assert payload.workload_identity == _declaration()
    assert _build_patch_kwargs([], payload)["workload_identity"] == _declaration()


def test_malformed_declaration_fails_direct_deploy() -> None:
    broken = _declaration()
    broken["roles"][0].pop("attestation")

    with pytest.raises(WorkloadIdentityDeclarationError):
        _direct_payload({"workload_identity": broken})
