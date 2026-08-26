"""Static validation for the closed scenario-matcher fact language."""

from __future__ import annotations

import json
import types
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Union, get_args, get_origin

from pydantic import BaseModel, TypeAdapter, ValidationError

from kamiwaza_sdk.validation.json_semantics import json_values_equal
from kamiwaza_sdk.validation.models import (
    ClusterFacts,
    DeploymentFacts,
    FactMatcher,
    InferenceTarget,
    MeshFacts,
    ScenarioDescriptor,
    ValidationIntent,
)
from kamiwaza_sdk.validation.provider import ProviderContractError

_FACT_ROOT_MODELS: dict[str, type[BaseModel]] = {
    "target": InferenceTarget,
    "cluster": ClusterFacts,
    "deployment": DeploymentFacts,
    "mesh": MeshFacts,
    "validation": ValidationIntent,
}
_MISSING = object()


def validate_descriptor_matchers(descriptor: ScenarioDescriptor) -> None:
    """Validate every matcher against the closed, versioned fact schema."""

    for matcher in descriptor.applies_when:
        annotation = _matcher_annotation(descriptor, matcher)
        _validate_operator_contract(annotation, matcher)


def _matcher_annotation(
    descriptor: ScenarioDescriptor, matcher: FactMatcher
) -> object:
    root_name, *parts = matcher.path
    root_model = _FACT_ROOT_MODELS.get(root_name)
    if root_model is None:
        raise ProviderContractError("descriptor matcher uses an invalid fact root")
    if root_name == "target" and descriptor.target_scope != "inference_target":
        raise ProviderContractError("descriptor matcher uses an invalid fact root")
    return _resolve_annotation(root_model, parts)


def _resolve_annotation(annotation: object, parts: Sequence[str]) -> object:
    if not parts:
        return annotation
    annotation = _unwrap_annotated(annotation)
    if _is_union(annotation):
        return _resolve_union_annotation(annotation, parts)
    if _is_model_annotation(annotation):
        return _resolve_model_annotation(annotation, parts)
    return _resolve_container_annotation(annotation, parts)


def _is_model_annotation(annotation: object) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _resolve_model_annotation(annotation: object, parts: Sequence[str]) -> object:
    if not isinstance(annotation, type) or not issubclass(annotation, BaseModel):
        raise ProviderContractError("descriptor matcher references a missing fact")
    field = annotation.model_fields.get(parts[0])
    if field is None:
        raise ProviderContractError("descriptor matcher references a missing fact")
    return _resolve_annotation(field.rebuild_annotation(), parts[1:])


def _resolve_container_annotation(annotation: object, parts: Sequence[str]) -> object:
    origin = get_origin(annotation)
    if origin in (tuple, list, Sequence):
        return _resolve_annotation(get_args(annotation)[0], parts)
    if origin in (dict, Mapping):
        key_annotation, value_annotation = get_args(annotation)
        if not _annotation_accepts(key_annotation, parts[0]):
            raise ProviderContractError(
                "descriptor matcher has incompatible value types"
            )
        return _resolve_annotation(value_annotation, parts[1:])
    raise ProviderContractError("descriptor matcher references a missing fact")


def _resolve_union_annotation(annotation: object, parts: Sequence[str]) -> object:
    for option in get_args(annotation):
        if option is type(None):
            continue
        try:
            return _resolve_annotation(option, parts)
        except ProviderContractError:
            continue
    raise ProviderContractError("descriptor matcher references a missing fact")


def _validate_operator_contract(annotation: object, matcher: FactMatcher) -> None:
    validator = _OPERATOR_VALIDATORS[matcher.operator]
    if not validator(annotation, matcher.value):
        raise ProviderContractError("descriptor matcher has incompatible value types")


def _valid_equal(annotation: object, value: object) -> bool:
    return _annotation_accepts(annotation, value)


def _valid_membership(annotation: object, value: object) -> bool:
    options = _json_array(value)
    return bool(options) and all(
        _annotation_accepts(annotation, option) for option in options
    )


def _valid_contains(annotation: object, value: object) -> bool:
    contained = _contained_annotation(annotation)
    return contained is not _MISSING and _annotation_accepts(contained, value)


def _valid_ordered(annotation: object, value: object) -> bool:
    return _annotation_is_numeric(annotation) and _is_json_number(value)


_OperatorValidator = Callable[[object, object], bool]
_OPERATOR_VALIDATORS: dict[str, _OperatorValidator] = {
    "eq": _valid_equal,
    "in": _valid_membership,
    "contains": _valid_contains,
    "gte": _valid_ordered,
    "lte": _valid_ordered,
}


def _annotation_accepts(annotation: object, value: object) -> bool:
    try:
        payload = json.dumps(value, allow_nan=False, separators=(",", ":"))
        normalized: object = TypeAdapter(annotation).validate_json(payload)
    except (TypeError, ValueError, ValidationError):
        return False
    return json_values_equal(value, normalized)


def _contained_annotation(annotation: object) -> object:
    annotation = _unwrap_annotated(annotation)
    if _is_union(annotation):
        return _contained_union_annotation(annotation)
    if annotation is str:
        return str
    origin = get_origin(annotation)
    if origin in (tuple, list, Sequence):
        return get_args(annotation)[0]
    if origin in (dict, Mapping):
        return get_args(annotation)[0]
    return _MISSING


def _contained_union_annotation(annotation: object) -> object:
    for option in get_args(annotation):
        if option is type(None):
            continue
        contained = _contained_annotation(option)
        if contained is not _MISSING:
            return contained
    return _MISSING


def _annotation_is_numeric(annotation: object) -> bool:
    annotation = _unwrap_annotated(annotation)
    if not _is_union(annotation):
        return annotation in (int, float)
    options = [option for option in get_args(annotation) if option is not type(None)]
    return bool(options) and all(_annotation_is_numeric(option) for option in options)


def _is_json_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _unwrap_annotated(annotation: object) -> object:
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def _is_union(annotation: object) -> bool:
    return get_origin(annotation) in (Union, types.UnionType)


def _json_array(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()
