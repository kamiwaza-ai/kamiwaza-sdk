"""Published JSON Schema catalog stays aligned with the Python contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from kamiwaza_sdk.validation.schema_export import (
    SCHEMA_MODELS,
    load_packaged_schema,
    schema_document,
)

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


def test_schema_files_are_included_in_the_source_tree() -> None:
    schema_dir = Path(__file__).parents[3] / "kamiwaza_sdk/validation/schemas"

    assert len(list(schema_dir.glob("*.schema.json"))) == len(EXPECTED_SCHEMA_IDS)
