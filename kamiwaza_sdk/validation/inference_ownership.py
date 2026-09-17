"""Ownership validation and reconciliation for inference deployments."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from kamiwaza_sdk.validation.inference_evidence import safe_error
from kamiwaza_sdk.validation.inference_runtime import InferenceCluster
from kamiwaza_sdk.validation.models import (
    CleanupResult,
    FixtureMutation,
    FixtureState,
)
from kamiwaza_sdk.validation.provider import ProviderContractError


def deployment_resources(state: FixtureState) -> tuple[FixtureMutation, ...]:
    resources: dict[tuple[str, str, str], FixtureMutation] = {}
    for item in state.journal:
        if item.resource_type != "model-deployment":
            continue
        key = (item.target_id, item.resource_type, item.resource_id)
        resources[key] = _next_deployment_resource(resources.get(key), item)
    ordered = tuple(resources[key] for key in sorted(resources))
    if _has_duplicate_targets(ordered):
        raise ProviderContractError("fixture target has multiple model deployments")
    return ordered


def _next_deployment_resource(
    previous: FixtureMutation | None,
    current: FixtureMutation,
) -> FixtureMutation:
    if _starts_deployment_ownership(previous, current):
        return current
    if _removes_owned_deployment(previous, current):
        return current
    raise ProviderContractError("invalid model deployment ownership transition")


def _starts_deployment_ownership(
    previous: FixtureMutation | None,
    current: FixtureMutation,
) -> bool:
    return previous is None and current.action in {"created", "adopted"}


def _removes_owned_deployment(
    previous: FixtureMutation | None,
    current: FixtureMutation,
) -> bool:
    if previous is None:
        return False
    if previous.action != "created":
        return False
    return current.action == "removed"


def _has_duplicate_targets(resources: tuple[FixtureMutation, ...]) -> bool:
    targets = [item.target_id for item in resources]
    return len(targets) != len(set(targets))


def owned_deployment_id(
    state: FixtureState,
    target_id: str,
    stored_target: Mapping[str, Any],
) -> str | None:
    opaque_id = _optional_text(stored_target.get("deployment_id"))
    resources = tuple(
        item for item in deployment_resources(state) if item.target_id == target_id
    )
    if not resources and opaque_id is None:
        return None
    if not _is_exact_owned_deployment(resources, opaque_id):
        raise ProviderContractError("fixture state has no exact owned deployment")
    return resources[0].resource_id


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _is_exact_owned_deployment(
    resources: tuple[FixtureMutation, ...], opaque_id: str | None
) -> bool:
    if len(resources) != 1:
        return False
    if resources[0].action != "created":
        return False
    return opaque_id == resources[0].resource_id


def reconcile_deployment(
    cluster: InferenceCluster, mutation: FixtureMutation
) -> CleanupResult:
    if mutation.action == "adopted":
        return _cleanup_result(mutation, "retained_foreign")
    if mutation.action == "removed":
        return cleanup_failure(
            mutation,
            ProviderContractError("deployment was already marked removed"),
        )
    try:
        if not cluster.is_active(mutation.resource_id):
            return _cleanup_result(mutation, "absent")
        stopped = cluster.stop(mutation.resource_id)
        if not stopped or cluster.is_active(mutation.resource_id):
            raise RuntimeError("owned deployment remains active")
    except Exception as exc:
        return cleanup_failure(mutation, exc)
    return _cleanup_result(mutation, "removed")


def _cleanup_result(
    mutation: FixtureMutation,
    status: Literal["removed", "absent", "retained_foreign", "failed"],
) -> CleanupResult:
    return CleanupResult(
        target_id=mutation.target_id,
        resource_type=mutation.resource_type,
        resource_id=mutation.resource_id,
        status=status,
        detail=None,
    )


def cleanup_failure(mutation: FixtureMutation, exc: Exception) -> CleanupResult:
    return CleanupResult(
        target_id=mutation.target_id,
        resource_type=mutation.resource_type,
        resource_id=mutation.resource_id,
        status="failed",
        detail=safe_error("cleanup", exc),
    )


def compensate_unjournaled_deployment(
    cluster: InferenceCluster, deployment_id: str
) -> None:
    try:
        cluster.stop(deployment_id)
    except Exception:
        pass
    try:
        remains_active = cluster.is_active(deployment_id)
    except Exception:
        raise ProviderContractError(
            "deployment journal failed and cleanup could not be verified"
        ) from None
    if remains_active:
        raise ProviderContractError(
            "deployment journal failed and deployment remains active"
        )


def close_cluster(cluster: InferenceCluster) -> None:
    try:
        cluster.close()
    except Exception:
        pass
