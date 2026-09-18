from __future__ import annotations

import uuid
import warnings
from enum import Enum
from typing import Literal, Optional

import pytest
from pydantic import BaseModel

from kamiwaza_sdk.agent_tools.schemas import (
    input_schema,
    result_schema,
    schemas_for,
    underivable,
)
from kamiwaza_sdk.agent_tools.spec_index import build_index

pytestmark = pytest.mark.unit


class Flavour(str, Enum):
    FAST = "fast"
    CHEAP = "cheap"


class Payload(BaseModel):
    name: str
    replicas: int = 1


class Sample:
    def primitives(
        self, name: str, count: int, ratio: float, flag: bool, ident: uuid.UUID
    ) -> None:
        """Take one of each primitive."""

    def optional(self, name: Optional[str] = None) -> None:
        """Take an optional string."""

    def literals(self, mode: Literal["a", "b"]) -> None:
        """Take a literal."""

    def enums(self, flavour: Flavour) -> None:
        """Take an enum."""

    def containers(self, names: list[str], tags: dict[str, int]) -> None:
        """Take containers."""

    def model(self, payload: Payload) -> Payload:
        """Take and return a model."""

    def listed(self) -> list[Payload]:
        """Return a list of models."""

    def unannotated(self, thing) -> None:  # noqa: ANN001 - deliberately untyped
        """Take an unannotated argument."""

    def unresolvable(self, value: "NotImportable") -> None:  # noqa: F821
        """Take an annotation that names nothing importable."""


def _properties(method) -> dict:
    schema, _ = input_schema(method)
    return schema["properties"]


def test_primitives_map_to_json_types() -> None:
    props = _properties(Sample.primitives)
    assert props["name"] == {"type": "string"}
    assert props["count"] == {"type": "integer"}
    assert props["ratio"] == {"type": "number"}
    assert props["flag"] == {"type": "boolean"}
    assert props["ident"] == {"type": "string", "format": "uuid"}


def test_input_rejects_unknown_fields():
    """FR-007: an agent that invents an argument must be told, not ignored."""
    schema, _ = input_schema(Sample.primitives)
    assert schema["additionalProperties"] is False


def test_required_is_the_parameters_without_defaults() -> None:
    schema, _ = input_schema(Sample.primitives)
    assert schema["required"] == ["name", "count", "ratio", "flag", "ident"]
    optional, _ = input_schema(Sample.optional)
    assert optional["required"] == []


def test_optional_collapses_to_a_nullable_type() -> None:
    """Optional[str] is the client's commonest shape; anyOf reads worse."""
    assert _properties(Sample.optional)["name"] == {"type": ["string", "null"]}


def test_literal_and_enum_become_enumerations() -> None:
    assert _properties(Sample.literals)["mode"] == {"enum": ["a", "b"]}
    assert _properties(Sample.enums)["flavour"] == {"enum": ["fast", "cheap"]}


def test_containers_carry_their_member_schemas() -> None:
    props = _properties(Sample.containers)
    assert props["names"] == {"type": "array", "items": {"type": "string"}}
    assert props["tags"] == {
        "type": "object",
        "additionalProperties": {"type": "integer"},
    }


def test_a_model_parameter_uses_the_models_own_schema() -> None:
    payload = _properties(Sample.model)["payload"]
    assert payload["properties"]["name"]["type"] == "string"
    assert payload["required"] == ["name"]
    assert payload["additionalProperties"] is False


def test_results_are_permissive_where_inputs_are_strict() -> None:
    """The asymmetry is the point: a field added next release must not break a
    working integration, but a field an agent invented must be refused."""
    arguments, _ = input_schema(Sample.model)
    result, _ = result_schema(Sample.model)
    assert arguments["properties"]["payload"]["additionalProperties"] is False
    assert "additionalProperties" not in result


def test_a_list_result_describes_its_items() -> None:
    result, derived = result_schema(Sample.listed)
    assert derived
    assert result["type"] == "array"
    assert "properties" in result["items"] or "$ref" in result["items"]


def test_an_unannotated_argument_is_unconstrained_and_reported() -> None:
    """An empty schema is honest; a guessed constraint is not."""
    schema, derived = input_schema(Sample.unannotated)
    assert schema["properties"]["thing"] == {}
    assert derived is False


def test_an_unresolvable_annotation_degrades_rather_than_raising() -> None:
    schema, derived = input_schema(Sample.unresolvable)
    assert derived is False
    assert schema["additionalProperties"] is False


def test_a_void_return_is_null_not_absent() -> None:
    result, derived = result_schema(Sample.primitives)
    assert derived
    assert result == {"type": "null"}


@pytest.fixture(scope="module")
def client():
    from kamiwaza_sdk.client import KamiwazaClient

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return KamiwazaClient(base_url="http://localhost:7777/api")


@pytest.fixture(scope="module")
def index(client):
    return build_index(client)


def test_every_published_operation_gets_both_schemas(index, client) -> None:
    for entry in index.published:
        service = getattr(client, entry.service, None)
        if service is None:
            continue
        schemas = schemas_for(entry, service)
        assert schemas.input["type"] == "object"
        assert schemas.input["additionalProperties"] is False
        assert isinstance(schemas.result, dict)


def test_only_the_known_annotation_gap_is_underivable(index, client) -> None:
    """One method annotates a module alias that is not importable at runtime.

    Asserted by name rather than by count so a *new* gap fails here instead of
    hiding inside a tolerated number.
    """
    assert underivable(index, client) == ("retrieval.flight_batches",)
