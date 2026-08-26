"""Canonical equality for values crossing the JSON protocol boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeGuard

from pydantic import BaseModel


def json_values_equal(left: object, right: object) -> bool:
    """Compare Python values by their lossless JSON representation."""

    left = _model_payload(left)
    right = _model_payload(right)
    if _is_json_number(left) and _is_json_number(right):
        return left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return _json_mappings_equal(left, right)
    if _is_json_array(left) and _is_json_array(right):
        return _json_arrays_equal(left, right)
    if type(left) is not type(right):
        return False
    return left == right


def _json_mappings_equal(
    left: Mapping[object, object], right: Mapping[object, object]
) -> bool:
    return left.keys() == right.keys() and all(
        json_values_equal(left[key], right[key]) for key in left
    )


def _json_arrays_equal(left: Sequence[object], right: Sequence[object]) -> bool:
    return len(left) == len(right) and all(
        json_values_equal(left_item, right_item)
        for left_item, right_item in zip(left, right, strict=True)
    )


def _model_payload(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _is_json_array(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _is_json_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
