"""Deterministic scenario applicability over validation-profile facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from kamiwaza_sdk.validation.models import (
    ClusterFacts,
    FactMatcher,
    InferenceTarget,
    ScenarioDescriptor,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import ProviderContractError


@dataclass(frozen=True)
class ApplicableTarget:
    """One descriptor candidate bound to its runtime cluster."""

    target_id: str
    cluster_id: str
    required: bool


@dataclass(frozen=True)
class _CandidateContext:
    target_id: str
    cluster: ClusterFacts
    inference_target: InferenceTarget | None
    required: bool


_LEVEL_ORDER = {"smoke": 0, "standard": 1, "comprehensive": 2}


def descriptor_is_active(
    profile: ValidationProfile, descriptor: ScenarioDescriptor
) -> bool:
    """Return whether level/overrides activate a descriptor for resolution."""

    scenario_id = descriptor.scenario_id
    if scenario_id in profile.validation.exclude:
        return False
    if scenario_id in profile.validation.include:
        return True
    return (
        _LEVEL_ORDER[profile.validation.level]
        >= _LEVEL_ORDER[descriptor.minimum_level]
    )


def applicable_targets(
    profile: ValidationProfile, descriptor: ScenarioDescriptor
) -> tuple[ApplicableTarget, ...]:
    """Return profile targets whose candidate views satisfy every matcher."""

    matches = (
        candidate
        for candidate in _candidate_contexts(profile, descriptor)
        if all(_matches(profile, candidate, matcher) for matcher in descriptor.applies_when)
    )
    return tuple(
        ApplicableTarget(
            target_id=item.target_id,
            cluster_id=item.cluster.id,
            required=item.required,
        )
        for item in matches
    )


def _candidate_contexts(
    profile: ValidationProfile, descriptor: ScenarioDescriptor
) -> tuple[_CandidateContext, ...]:
    clusters = {cluster.id: cluster for cluster in profile.clusters}
    if descriptor.target_scope == "inference_target":
        return tuple(
            _CandidateContext(
                target_id=target.id,
                cluster=clusters[target.cluster_id],
                inference_target=target,
                required=target.required,
            )
            for target in profile.inference_targets
        )
    return tuple(
        _CandidateContext(
            target_id=cluster.id,
            cluster=cluster,
            inference_target=None,
            required=True,
        )
        for cluster in profile.clusters
    )


def _matches(
    profile: ValidationProfile,
    candidate: _CandidateContext,
    matcher: FactMatcher,
) -> bool:
    root_name, *parts = matcher.path.split(".")
    if not parts:
        raise ProviderContractError("descriptor matcher references a missing fact")
    root = _matcher_root(profile, candidate, root_name)
    values = _path_values(root, parts)
    if not values:
        raise ProviderContractError("descriptor matcher references a missing fact")
    return _apply_operator(values, matcher)


def _matcher_root(
    profile: ValidationProfile, candidate: _CandidateContext, root_name: str
) -> object:
    roots: dict[str, object | None] = {
        "target": candidate.inference_target,
        "cluster": candidate.cluster,
        "deployment": profile.deployment,
        "mesh": profile.mesh,
        "validation": profile.validation,
    }
    if root_name not in roots or roots[root_name] is None:
        raise ProviderContractError("descriptor matcher uses an invalid fact root")
    return roots[root_name]


def _path_values(root: object, parts: Sequence[str]) -> list[object]:
    values = [root]
    for part in parts:
        values = [child for value in values for child in _resolve_part(value, part)]
    return values


def _resolve_part(value: object, part: str) -> list[object]:
    if isinstance(value, BaseModel):
        return [getattr(value, part)] if hasattr(value, part) else []
    if isinstance(value, Mapping):
        return [value[part]] if part in value else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [child for item in value for child in _resolve_part(item, part)]
    return []


def _apply_operator(values: Sequence[object], matcher: FactMatcher) -> bool:
    matcher_fn = _OPERATOR_MATCHERS[matcher.operator]
    return matcher_fn(values, matcher.value)


def _matches_equal(values: Sequence[object], expected: object) -> bool:
    return any(value == expected for value in values)


def _matches_in(values: Sequence[object], expected: object) -> bool:
    options = _require_collection(expected)
    return any(value in options for value in values)


def _matches_contains(values: Sequence[object], expected: object) -> bool:
    return any(_contains(value, expected) for value in values)


def _matches_gte(values: Sequence[object], expected: object) -> bool:
    threshold = _require_number(expected)
    return any(_require_number(value) >= threshold for value in values)


def _matches_lte(values: Sequence[object], expected: object) -> bool:
    threshold = _require_number(expected)
    return any(_require_number(value) <= threshold for value in values)


_OperatorMatcher = Callable[[Sequence[object], object], bool]
_OPERATOR_MATCHERS: dict[str, _OperatorMatcher] = {
    "eq": _matches_equal,
    "in": _matches_in,
    "contains": _matches_contains,
    "gte": _matches_gte,
    "lte": _matches_lte,
}


def _require_collection(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    raise ProviderContractError("descriptor matcher has incompatible value types")


def _contains(value: object, expected: object) -> bool:
    if isinstance(value, str) and isinstance(expected, str):
        return expected in value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return expected in value
    raise ProviderContractError("descriptor matcher has incompatible value types")


def _require_number(value: object) -> int | float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    raise ProviderContractError("descriptor matcher has incompatible value types")
