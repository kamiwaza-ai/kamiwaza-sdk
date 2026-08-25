"""Wire-schema constraints stay aligned with canonical runtime validation."""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from kamiwaza_sdk.validation import RuntimeContext, ScenarioCatalog, ScenarioPlan
from kamiwaza_sdk.validation.golden_provider import GoldenProvider
from kamiwaza_sdk.validation.models import ValidationProfile
from kamiwaza_sdk.validation.schema_export import load_packaged_schema

from .support import profile_payload

pytestmark = pytest.mark.contract


def _schema_rejects(schema_id: str, payload: Any) -> bool:
    schema = load_packaged_schema(schema_id)
    return bool(list(Draft202012Validator(schema).iter_errors(payload)))


def test_runtime_context_rejects_url_userinfo_without_exposing_it() -> None:
    secret = "supersecret"
    payload = {
        "schema": "kamiwaza.runtime-context/v1",
        "run_id": "run-123",
        "clusters": [
            {
                "id": "evo-x2-2",
                "base_url": f"https://user:{secret}@cluster.example.test/api",
                "api_key_ref": "secret://evo-x2-2/admin-pat",
                "kubeconfig_ref": "file:///run/secrets/evo-x2-2.kubeconfig",
            }
        ],
    }

    with pytest.raises(ValidationError, match="base_url") as error:
        RuntimeContext.model_validate(payload)

    assert secret not in str(error.value)
    assert secret not in str(error.value.errors(include_input=False))
    assert _schema_rejects("kamiwaza.runtime-context/v1", payload)


def test_runtime_context_schema_accepts_explicit_null_ownership_key() -> None:
    payload = {
        "schema": "kamiwaza.runtime-context/v1",
        "run_id": "run-123",
        "ownership_key_ref": None,
        "clusters": [
            {
                "id": "evo-x2-2",
                "base_url": "https://cluster.example.test/api",
                "api_key_ref": "secret://evo-x2-2/admin-pat",
                "kubeconfig_ref": "file:///run/secrets/evo-x2-2.kubeconfig",
            }
        ],
    }

    RuntimeContext.model_validate(payload)

    assert not _schema_rejects("kamiwaza.runtime-context/v1", payload)


def test_packaged_schemas_reject_trailing_newlines_rejected_by_runtime() -> None:
    identifier_profile = profile_payload()
    identifier_profile["clusters"][0]["id"] = "evo-x2-2\n"  # type: ignore[index]
    with pytest.raises(ValidationError):
        ValidationProfile.model_validate(identifier_profile)
    assert _schema_rejects("kamiwaza.validation-profile/v1", identifier_profile)

    profile = profile_payload()
    profile["clusters"][0]["features"] = {"gpu\n": True}  # type: ignore[index]
    with pytest.raises(ValidationError):
        ValidationProfile.model_validate(profile)
    assert _schema_rejects("kamiwaza.validation-profile/v1", profile)

    descriptor = GoldenProvider().describe()[0].model_dump(mode="json")
    descriptor["applies_when"][0]["path"][1] = "engine\n"
    with pytest.raises(ValidationError):
        ScenarioCatalog.model_validate([descriptor])
    assert _schema_rejects("kamiwaza.scenario-catalog/v1", [descriptor])

    source_profile = profile_payload()
    source_profile["validation"]["include"] = ["sdk.golden.echo/v1"]  # type: ignore[index]
    plan = (
        GoldenProvider()
        .resolve(ValidationProfile.model_validate(source_profile))
        .model_dump(mode="json", by_alias=True)
    )
    plan["profile_digest"] += "\n"
    with pytest.raises(ValidationError):
        ScenarioPlan.model_validate(plan)
    assert _schema_rejects("kamiwaza.scenario-plan/v1", plan)
