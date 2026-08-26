"""Canonical JSON Schema catalog for validation-provider documents."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from pydantic import BaseModel

from kamiwaza_sdk.validation.models import (
    CleanupEvidence,
    CoverageSummary,
    FixtureState,
    RuntimeContext,
    ScenarioCatalog,
    ScenarioEvidence,
    ScenarioPlan,
    ValidationProfile,
)

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "kamiwaza.validation-profile/v1": ValidationProfile,
    "kamiwaza.scenario-catalog/v1": ScenarioCatalog,
    "kamiwaza.scenario-plan/v1": ScenarioPlan,
    "kamiwaza.runtime-context/v1": RuntimeContext,
    "kamiwaza.fixture-state/v1": FixtureState,
    "kamiwaza.scenario-evidence/v1": ScenarioEvidence,
    "kamiwaza.cleanup-evidence/v1": CleanupEvidence,
    "kamiwaza.coverage-summary/v1": CoverageSummary,
}

SCHEMA_FILENAMES = {
    schema_id: schema_id.removeprefix("kamiwaza.").replace("/", ".") + ".schema.json"
    for schema_id in SCHEMA_MODELS
}


def schema_document(schema_id: str, model_type: type[BaseModel]) -> dict[str, Any]:
    """Build one Draft 2020-12 schema with its stable wire identifier."""

    document = model_type.model_json_schema(by_alias=True, mode="validation")
    document["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    document["$id"] = schema_id
    document["$comment"] = (
        "JSON Schema enforces representable wire constraints; canonical SDK runtime "
        "model validation is required for cross-field identity, reference, journal, "
        "and status invariants that JSON Schema cannot express."
    )
    return document


def load_packaged_schema(schema_id: str) -> dict[str, Any]:
    """Load a committed schema by its stable wire identifier."""

    filename = SCHEMA_FILENAMES[schema_id]
    resource = files("kamiwaza_sdk.validation.schemas").joinpath(filename)
    return json.loads(resource.read_text(encoding="utf-8"))
