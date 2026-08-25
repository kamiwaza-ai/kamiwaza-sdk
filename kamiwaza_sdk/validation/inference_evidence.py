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


def residual_outcome(
    cluster: InferenceCluster, deployment_id: str
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if cluster.is_active(deployment_id):
            raise RuntimeError("run-owned deployment remains active")
    except Exception as exc:
        return failure("residual-cleanup", exc, elapsed_ms(started))
    return passed(started)


def runtime_evidence(stored_target: Mapping[str, Any]) -> dict[str, Any]:
    return dict(mapping(stored_target.get("runtime"), "target runtime"))


def owned_deployments(state: FixtureState) -> tuple[FixtureMutation, ...]:
    resources: dict[tuple[str, str, str], FixtureMutation] = {}
    for item in state.journal:
        if item.resource_type == "model-deployment":
            resources[(item.target_id, item.resource_type, item.resource_id)] = item
    return tuple(resources[key] for key in sorted(resources))


def reconcile_deployment(
    cluster: InferenceCluster, mutation: FixtureMutation
) -> CleanupResult:
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
