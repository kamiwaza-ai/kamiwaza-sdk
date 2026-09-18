"""Shared federation contract inputs and missing-resource behavior."""

from __future__ import annotations

from pathlib import Path

from kamiwaza_sdk.validation import RuntimeContext, ValidationProfile
from kamiwaza_sdk.validation.federation_spec import FEDERATION_SCENARIO_ID
from tests.contract.validation.support import profile_payload


def _profile() -> ValidationProfile:
    payload = profile_payload()
    payload["validation"] = {
        "level": "smoke",
        "fixture_mode": "owned",
        "include": [FEDERATION_SCENARIO_ID],
        "exclude": [],
    }
    payload["clusters"] = [
        {
            "id": "edge-a",
            "roles": ["controller"],
            "node_count": 1,
            "hardware": {"accelerators": []},
            "features": {"rebac": True},
        },
        {
            "id": "edge-b",
            "roles": ["controller"],
            "node_count": 1,
            "hardware": {"accelerators": []},
            "features": {"rebac": True},
        },
    ]
    payload["mesh"] = {
        "edges": [
            {"initiator": "edge-a", "receiver": "edge-b", "identity_mode": "shared_idp"}
        ]
    }
    payload.pop("inference_targets", None)
    return ValidationProfile.model_validate(payload)


def _runtime(tmp_path: Path) -> RuntimeContext:
    ownership = tmp_path / "ownership.key"
    ownership.write_bytes(b"o" * 48)
    ownership.chmod(0o600)
    password = tmp_path / "persona.password"
    password.write_text("persona-secret\n", encoding="utf-8")
    return RuntimeContext.model_validate(
        {
            "schema": "kamiwaza.runtime-context/v1",
            "run_id": "run-federation-1",
            "ownership_key_ref": ownership.as_uri(),
            "secret_refs": {
                "shared-idp-admin-password": "file:///run/secrets/admin.password",
                "shared-idp-persona-password": password.as_uri(),
            },
            "clusters": [
                {
                    "id": "edge-a",
                    "base_url": "https://edge-a.test/api",
                    "api_key_ref": "file:///run/secrets/edge-a.api-key",
                    "kubeconfig_ref": "file:///run/secrets/edge-a.kubeconfig",
                },
                {
                    "id": "edge-b",
                    "base_url": "https://edge-b.test/api",
                    "api_key_ref": "file:///run/secrets/edge-b.api-key",
                    "kubeconfig_ref": "file:///run/secrets/edge-b.kubeconfig",
                },
            ],
        }
    )


class _NotFound(RuntimeError):
    status_code = 404
