"""Contract tests for the optional delegated-workload manifest declaration."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


_CONTRACT_SHA256 = "dc16181edefdbca44d7e5a49637282925a181c8171bb9a566dde15e411cf2f45"


def _schema_bytes() -> bytes:
    resource = files("kamiwaza_extensions.validators").joinpath(
        "workload_identity_manifest.schema.json"
    )
    return resource.read_bytes()


def _validator() -> Draft202012Validator:
    schema = json.loads(_schema_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _valid_declaration() -> dict[str, Any]:
    return {
        "workload_identity": {
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
            "required_resource_contracts": [
                {
                    "resource_type": "example.document",
                    "descriptor_versions": ["v1"],
                    "guard_versions": ["v1"],
                }
            ],
            "roles": [
                {
                    "id": "executor",
                    "type": "executor",
                    "service": "worker",
                    "attestation": {
                        "profiles": [
                            "kubernetes-tokenreview-v1",
                            "kubernetes-offline-v1",
                        ],
                        "audience": "kamiwaza-workload-sts",
                        "ttl_seconds": 600,
                    },
                    "platform_operations": [
                        "run:claim",
                        "run:start",
                        "run:heartbeat",
                        "run:transition",
                    ],
                }
            ],
        }
    }


def test_packaged_schema_matches_the_normative_contract() -> None:
    assert hashlib.sha256(_schema_bytes()).hexdigest() == _CONTRACT_SHA256


def test_declaration_is_optional_and_complete_v1_is_valid() -> None:
    validator = _validator()
    validator.validate({"name": "legacy-extension"})
    validator.validate(_valid_declaration())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["workload_identity"]["roles"][0]["attestation"].update(
            {"profiles": ["kubernetes-tokenreview-v1"]}
        ),
        lambda value: value["workload_identity"]["roles"][0].update(
            {"platform_operations": ["admin:anything"]}
        ),
        lambda value: value["workload_identity"]["roles"][0].update(
            {"unregistered_authority": True}
        ),
        lambda value: value["workload_identity"]["roles"][0]["attestation"].update(
            {"ttl_seconds": 601}
        ),
    ],
)
def test_authority_broadening_or_nonportable_profiles_are_rejected(mutation) -> None:
    declaration = _valid_declaration()
    mutation(declaration)

    with pytest.raises(ValidationError):
        _validator().validate(declaration)
