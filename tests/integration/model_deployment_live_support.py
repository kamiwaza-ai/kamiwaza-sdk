"""Shared helpers for the ENG-12327 (T09) llama.cpp deployment live tests.

``test_model_deployment_lifecycle_live.py`` and
``test_openai_compatible_inference_live.py`` hold one test each, so evidence
credited per test file covers only what that file's test ran.

Both tests exercise the llama.cpp lane ENG-12327 chose for 1.2.1. The target is
the explicit fleet target when ``KAMIWAZA_TEST_LLM_REPO`` /
``KAMIWAZA_TEST_LLM_ENGINE`` name a llama.cpp model, and otherwise the suite's
``GGUF_LLM_TARGET``; an explicit fleet target on another engine is skipped as
not applicable. Before creating anything each test confirms the target's GGUF
weights and at least one model config are already on the cluster: a missing
prerequisite skips an optional target and fails a fleet-required one. Neither
test calls an SDK download method.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, NoReturn
from uuid import UUID

import pytest
from kamiwaza_sdk.exceptions import KamiwazaError, NotFoundError
from model_targets import InferenceTarget, select_inference_target
from pydantic import ValidationError as SchemaValidationError

LANE_ENGINE = "llamacpp"
READY_TIMEOUT_SECONDS = 900
STOP_TIMEOUT_SECONDS = 300


@dataclass
class DeploymentRegistry:
    """Resources a test created and has not yet proven cleaned up.

    Deployments are registered only after their id is shown to be new, and
    configs with the unique name they were created under. Each leaves the
    registry only once its stop or deletion is confirmed.
    """

    deployment_ids: list[UUID] = field(default_factory=list)
    configs: dict[UUID, str] = field(default_factory=dict)


def stop_and_wait(client, deployment_id: UUID, *, force: bool) -> None:
    """Stop a deployment and wait for STOPPED.

    The test body stops without force: on 1.2.1 a forced stop routes a failed
    stop to a cleanup that marks the row STOPPED and returns True, so only an
    unforced stop can show that stopping works. Teardown forces.
    """
    assert (
        client.serving.stop_deployment(deployment_id=deployment_id, force=force) is True
    )
    client.serving.wait_for_deployment(
        deployment_id,
        desired_status=("STOPPED",),
        failure_status=("FAILED", "ERROR"),
        poll_interval=5.0,
        timeout=STOP_TIMEOUT_SECONDS,
    )


def cleanup_failures(client, registry: DeploymentRegistry) -> list[str]:
    """Stop and delete every registered resource, collecting failures instead of
    stopping at the first one. A config is deleted only if it still carries the
    name the test created it with, and its deletion is proven by NotFound."""
    failures: list[str] = []
    for deployment_id in reversed(registry.deployment_ids):
        try:
            stop_and_wait(client, deployment_id, force=True)
        except (
            AssertionError,
            KamiwazaError,
            SchemaValidationError,
            TimeoutError,
        ) as exc:
            failures.append(f"could not stop deployment {deployment_id}: {exc!r}")
    for config_id, name in reversed(list(registry.configs.items())):
        try:
            current = client.models.get_model_config(config_id)
        except NotFoundError:
            continue
        except (KamiwazaError, SchemaValidationError) as exc:
            failures.append(
                f"could not read config {config_id} before cleanup: {exc!r}"
            )
            continue
        if current.name != name:
            failures.append(
                f"refused to delete config {config_id}: it is named "
                f"{current.name!r}, not {name!r}"
            )
            continue
        delete_answer = ""
        try:
            client.models.delete_model_config(config_id)
        except NotFoundError as exc:
            # On 1.2.1 a delete refused by the permission check also answers
            # 404, so NotFound here is not proof of absence: the read-back below
            # decides, and the 404 is kept for the failure message.
            delete_answer = f" (the delete answered {exc!r})"
        except (KamiwazaError, SchemaValidationError) as exc:
            failures.append(f"could not delete config {config_id}: {exc!r}")
            continue
        try:
            # The read masks a permission refusal as 404 too, but this client
            # read the config successfully just above, so NotFound means gone.
            client.models.get_model_config(config_id)
        except NotFoundError:
            continue
        except (KamiwazaError, SchemaValidationError) as exc:
            failures.append(
                f"could not confirm config {config_id} deleted: {exc!r}{delete_answer}"
            )
            continue
        failures.append(
            f"config {config_id} still readable after delete{delete_answer}"
        )
    return failures


def registry_with_cleanup(client) -> Iterator[DeploymentRegistry]:
    """Body of each test file's ``created`` fixture.

    Yields a registry, then cleans up what the test body did not prove cleaned
    up. All failures are raised together, which pytest reports as a teardown
    error beside the test outcome rather than in place of it.
    """
    registry = DeploymentRegistry()
    yield registry
    failures = cleanup_failures(client, registry)
    if failures:
        raise AssertionError("model cleanup incomplete: " + "; ".join(failures))


def lane_target() -> InferenceTarget:
    """The explicit fleet target if one is set, else the suite's GGUF target.

    Without a hardware snapshot, ``select_inference_target`` returns the explicit
    target when the fleet variables are set and ``GGUF_LLM_TARGET`` otherwise.
    """
    target = select_inference_target(None)
    if target.engine_name != LANE_ENGINE:
        pytest.skip(
            f"not applicable: the explicit fleet target {target.repo_id} uses engine "
            f"{target.engine_name!r}; this test covers the {LANE_ENGINE} lane"
        )
    return target


def ready_target(
    client, target: InferenceTarget, target_model_file_id
) -> tuple[Any, UUID]:
    """Return the target model and its ready GGUF weights file.

    Runs before anything is created. A missing optional target skips; a missing
    fleet-required target fails. It cannot tell an absent prerequisite from a
    listing that wrongly returns nothing.
    """
    model = next(
        (
            candidate
            for candidate in client.models.list_models(load_files=True)
            if candidate.repo_modelId == target.repo_id
        ),
        None,
    )
    file_id = target_model_file_id(model, target.quantization) if model else None
    if model is None or file_id is None:
        missing_prerequisite(
            target,
            f"prerequisite: GGUF weights for {target.repo_id} ({target.quantization}) "
            "are not already on this cluster; this test never downloads",
        )
    return model, UUID(file_id)


def missing_prerequisite(target: InferenceTarget, reason: str) -> NoReturn:
    """Fail for a fleet-required target, skip otherwise.

    Raises the exceptions ``pytest.fail`` / ``pytest.skip`` raise, so a type
    checker without pytest's stubs still sees that this never returns.
    """
    __tracebackhide__ = True
    if target.required:
        raise pytest.fail.Exception(msg=reason)
    raise pytest.skip.Exception(msg=reason)


def deploy_fresh(
    client, target: InferenceTarget, model, config_id: UUID, file_id: UUID
) -> UUID:
    """Deploy and return an id that did not exist anywhere before this call."""
    existing = {d.id for d in client.serving.list_deployments()}
    deployment_id = client.serving.deploy_model(
        model_id=model.id,
        m_config_id=config_id,
        m_file_id=file_id,
        engine_name=target.engine_name,
        lb_port=0,
        autoscaling=False,
        min_copies=1,
        starting_copies=1,
        wait=False,
    )
    assert isinstance(deployment_id, UUID), (
        f"deploy_model did not return a deployment UUID: {deployment_id!r}"
    )
    assert deployment_id not in existing, (
        f"deploy_model returned pre-existing deployment {deployment_id}"
    )
    return deployment_id


def assert_deployed_as_requested(
    deployment,
    deployment_id: UUID,
    target: InferenceTarget,
    config_id: UUID,
    file_id: UUID,
) -> None:
    assert (
        deployment.id,
        deployment.status,
        deployment.m_config_id,
        deployment.m_file_id,
        deployment.engine_name,
    ) == (deployment_id, "DEPLOYED", config_id, file_id, target.engine_name)
