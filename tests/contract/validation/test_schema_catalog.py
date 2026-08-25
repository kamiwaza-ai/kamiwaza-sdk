"""Published JSON Schema catalog stays aligned with the Python contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from kamiwaza_sdk.validation import ValidationProfile
from kamiwaza_sdk.validation.schema_export import (
    SCHEMA_MODELS,
    load_packaged_schema,
    schema_document,
)
from kamiwaza_sdk.validation.golden_provider import GoldenProvider

from .support import profile_payload

pytestmark = pytest.mark.contract


EXPECTED_SCHEMA_IDS = {
    "kamiwaza.validation-profile/v1",
    "kamiwaza.scenario-catalog/v1",
    "kamiwaza.scenario-plan/v1",
    "kamiwaza.runtime-context/v1",
    "kamiwaza.fixture-state/v1",
    "kamiwaza.scenario-evidence/v1",
    "kamiwaza.cleanup-evidence/v1",
    "kamiwaza.coverage-summary/v1",
}


def test_every_protocol_document_has_a_packaged_draft_2020_12_schema() -> None:
    assert set(SCHEMA_MODELS) == EXPECTED_SCHEMA_IDS

    for schema_id, model_type in SCHEMA_MODELS.items():
        packaged = load_packaged_schema(schema_id)
        assert packaged == schema_document(schema_id, model_type)
        assert packaged["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert packaged["$id"] == schema_id
        Draft202012Validator.check_schema(packaged)


def test_packaged_profile_schema_rejects_selector_fields() -> None:
    schema = load_packaged_schema("kamiwaza.validation-profile/v1")
    payload = profile_payload()
    payload["validation"]["markers"] = "requires_deployable_model"  # type: ignore[index]

    errors = list(Draft202012Validator(schema).iter_errors(payload))

    assert any("markers" in error.message for error in errors)


@pytest.mark.parametrize(
    ("schema_id", "payload"),
    [
        (
            "kamiwaza.validation-profile/v1",
            {**profile_payload(), "clusters": []},
        ),
        (
            "kamiwaza.validation-profile/v1",
            {
                **profile_payload(),
                "clusters": [
                    {
                        **profile_payload()["clusters"][0],  # type: ignore[index]
                        "roles": [],
                    }
                ],
            },
        ),
        ("kamiwaza.scenario-catalog/v1", []),
    ],
)
def test_packaged_schemas_reject_representable_empty_collection_invariants(
    schema_id: str, payload: Any
) -> None:
    schema = load_packaged_schema(schema_id)

    errors = list(Draft202012Validator(schema).iter_errors(payload))

    assert errors


def test_packaged_schemas_reject_representable_uniqueness_invariants() -> None:
    profile_schema = load_packaged_schema("kamiwaza.validation-profile/v1")
    profile = profile_payload()
    profile["clusters"][0]["roles"] = ["controller", "controller"]  # type: ignore[index]
    catalog_schema = load_packaged_schema("kamiwaza.scenario-catalog/v1")
    descriptor = GoldenProvider().describe()[0].model_dump(mode="json")
    descriptor["case_ids"] = ["echo", "echo"]

    assert list(Draft202012Validator(profile_schema).iter_errors(profile))
    assert list(Draft202012Validator(catalog_schema).iter_errors([descriptor]))


def test_packaged_schemas_reject_representable_cross_field_invariants() -> None:
    profile_data = profile_payload()
    profile_data["validation"]["include"] = ["sdk.golden.echo/v1"]  # type: ignore[index]
    plan = (
        GoldenProvider()
        .resolve(ValidationProfile.model_validate(profile_data))
        .model_dump(mode="json", by_alias=True)
    )
    plan["selected"][0]["case_ids"] = []
    cleanup = {
        "schema": "kamiwaza.cleanup-evidence/v1",
        "provider_revision": "sdk.golden@v1",
        "run_id": "run-123",
        "state_digest": "sha256:" + "1" * 64,
        "status": "passed",
        "results": [
            {
                "target_id": "target-1",
                "resource_type": "deployment",
                "resource_id": "deployment-1",
                "status": "failed",
                "detail": "resource remains",
            }
        ],
    }
    coverage = {
        "schema": "kamiwaza.coverage-summary/v1",
        "status": "failed",
        "plan_digest": "sha256:" + "2" * 64,
        "issues": [],
    }

    for schema_id, payload in (
        ("kamiwaza.scenario-plan/v1", plan),
        ("kamiwaza.cleanup-evidence/v1", cleanup),
        ("kamiwaza.coverage-summary/v1", coverage),
    ):
        schema = load_packaged_schema(schema_id)
        assert list(Draft202012Validator(schema).iter_errors(payload))


def test_packaged_schemas_require_canonical_runtime_model_validation() -> None:
    for schema_id in EXPECTED_SCHEMA_IDS:
        comment = load_packaged_schema(schema_id).get("$comment", "")
        assert "canonical SDK runtime model validation" in comment


def test_schema_files_are_included_in_the_source_tree() -> None:
    schema_dir = Path(__file__).parents[3] / "kamiwaza_sdk/validation/schemas"

    assert len(list(schema_dir.glob("*.schema.json"))) == len(EXPECTED_SCHEMA_IDS)
