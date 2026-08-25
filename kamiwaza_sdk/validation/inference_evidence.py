"""Evidence, outcome, and cleanup helpers for inference validation."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any, Literal

from kamiwaza_sdk.validation.inference_runtime import InferenceCluster
from kamiwaza_sdk.validation.models import (
    CaseResult,
    CleanupResult,
    FixtureMutation,
    FixtureState,
    ResolvedScenario,
)
from kamiwaza_sdk.validation.provider import ProviderContractError

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def target_state(state: FixtureState, target_id: str) -> Mapping[str, Any]:
    targets = mapping(state.opaque.get("targets"), "fixture targets")
    return mapping(targets.get(target_id), "fixture target")


def target_clusters(state: FixtureState) -> dict[str, str]:
    targets = mapping(state.opaque.get("targets"), "fixture targets")
    return {
        target_id: _cluster_id(mapping(value, "fixture target"))
        for target_id, value in targets.items()
    }


def _cluster_id(target: Mapping[str, Any]) -> str:
    cluster_id = target.get("cluster_id")
    if not isinstance(cluster_id, str) or not cluster_id:
        raise ProviderContractError("fixture target has invalid cluster_id")
    return cluster_id


def mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderContractError(f"{label} is invalid")
    return value


def stored_result(
    selected: ResolvedScenario,
    stored_target: Mapping[str, Any],
    case_id: str,
) -> CaseResult:
    phases = mapping(stored_target.get("phases"), "target phases")
    outcome = mapping(phases.get(case_id), "phase outcome")
    return case_result(selected, case_id, outcome)


def case_result(
    selected: ResolvedScenario, case_id: str, outcome: Mapping[str, Any]
) -> CaseResult:
    status = outcome.get("status")
    duration = outcome.get("duration_ms")
    detail = outcome.get("detail")
    if status not in {"passed", "failed"} or not isinstance(duration, int):
        raise ProviderContractError("phase outcome is invalid")
    if detail is not None and not isinstance(detail, str):
        raise ProviderContractError("phase detail is invalid")
    return CaseResult(
        target_id=selected.target_id,
        scenario_id=selected.scenario_id,
        case_id=case_id,
        status=status,
        duration_ms=duration,
        detail=detail,
    )


def chat_outcome(
    cluster: InferenceCluster,
    deployment_id: str,
    stored_target: Mapping[str, Any],
) -> dict[str, Any]:
    phases = mapping(stored_target.get("phases"), "target phases")
    readiness = mapping(phases.get("deployment-readiness"), "readiness phase")
    if readiness.get("status") != "passed":
        return failed("blocked by deployment-readiness")
    started = time.monotonic()
    try:
        first_messages = (
            {"role": "user", "content": "Reply with one short greeting."},
        )
        first = cluster.chat(deployment_id, first_messages).strip()
        if not first:
            raise RuntimeError("first chat turn was empty")
        second_messages = (
            first_messages[0],
            {"role": "assistant", "content": first},
            {"role": "user", "content": "Reply with one short farewell."},
        )
        if not cluster.chat(deployment_id, second_messages).strip():
            raise RuntimeError("second chat turn was empty")
    except Exception as exc:
        return failure("openai-multi-turn-chat", exc, elapsed_ms(started))
    return passed(started)


def stop_outcome(cluster: InferenceCluster, deployment_id: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if not cluster.stop(deployment_id):
            raise RuntimeError("platform did not confirm deployment stop")
    except Exception as exc:
        return failure("deployment-stop", exc, elapsed_ms(started))
    return passed(started)


def residual_outcome(cluster: InferenceCluster, deployment_id: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if cluster.is_active(deployment_id):
            raise RuntimeError("run-owned deployment remains active")
    except Exception as exc:
        return failure("residual-cleanup", exc, elapsed_ms(started))
    return passed(started)


def runtime_evidence(stored_target: Mapping[str, Any]) -> dict[str, Any]:
    return dict(mapping(stored_target.get("runtime"), "target runtime"))


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
    opaque_id = optional_text(stored_target.get("deployment_id"))
    resources = tuple(
        item for item in deployment_resources(state) if item.target_id == target_id
    )
    if not resources and opaque_id is None:
        return None
    if not _is_exact_owned_deployment(resources, opaque_id):
        raise ProviderContractError("fixture state has no exact owned deployment")
    return resources[0].resource_id


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


def required_image_digest(value: str | None) -> str:
    digest = _image_digest(value)
    if digest is None:
        raise RuntimeError("actual image digest is unavailable")
    return digest


def validate_expected_image(expected: str | None, actual: str | None) -> None:
    if expected is None:
        return
    if _image_digest(expected) != _image_digest(actual):
        raise RuntimeError("actual image does not match expected image")


def _image_digest(value: str | None) -> str | None:
    if value is None:
        return None
    match = _DIGEST_RE.search(value)
    return match.group(0) if match else None


def passed(started: float) -> dict[str, Any]:
    return {"status": "passed", "duration_ms": elapsed_ms(started), "detail": None}


def failed(detail: str) -> dict[str, Any]:
    return {"status": "failed", "duration_ms": 0, "detail": detail}


def failure(phase: str, exc: Exception, duration_ms: int) -> dict[str, Any]:
    return {
        "status": "failed",
        "duration_ms": duration_ms,
        "detail": safe_error(phase, exc),
    }


def safe_error(phase: str, exc: Exception) -> str:
    return f"{phase} failed ({type(exc).__name__})"


def elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
