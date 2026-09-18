"""Model configuration and local deployment lifecycle through the SDK (ENG-12327, T09).

The test exercises the llama.cpp lane ENG-12327 chose for 1.2.1. The target is
the explicit fleet target when ``KAMIWAZA_TEST_LLM_REPO`` /
``KAMIWAZA_TEST_LLM_ENGINE`` name a llama.cpp model, and otherwise the suite's
``GGUF_LLM_TARGET``; an explicit fleet target on another engine is skipped as
not applicable. Before creating anything the test confirms the target's GGUF
weights and at least one model config are already on the cluster: a missing
prerequisite skips an optional target and fails a fleet-required one. The test
calls no SDK download method.

A disposable model config is created, read, listed and updated; a fresh
deployment using it reaches DEPLOYED with the requested engine and weights file
and is checked through the deployment, active-deployment and instance methods;
it serves one chat completion through the OpenAI-compatible client the SDK
returns, asking for the sum of two numbers chosen for this run, and the reply
must contain the sum, which the prompt does not; captured and streamed logs are
read; it is stopped (without force) to STOPPED, and the config is deleted and
proven NotFound.

The chat completion shows the deployment serves requests. It does not claim
``models.openai-compatible-inference``: that capability's document allows a pass
only with a representative call per declared operation (chat, embeddings,
transcription and image generation), which develop's capability map enforces
since ENG-12269.

Deliberately not called: the log-pattern route, which on 1.2.1 reads only a
local log file or Kubernetes pod logs and, unlike the captured-log route, has no
host-spawner source, so it answers 404 for a deployment whose logs only the host
spawner holds. And ``serving.get_health``: on 1.2.1 it returns entries for
deployments still INITIALIZING or whose check finds a problem, but none for a
DEPLOYED local-engine deployment unless it checks that deployment's Ray Serve
route and finds it missing (it skips the check for Kubernetes-backed
deployments and for deployments without a Ray Serve binding), so no assertion
about this test's deployment could fail for a broken health check.
"""

from __future__ import annotations

import itertools
import re
import secrets
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, NoReturn, TypeVar
from uuid import UUID, uuid4

import pytest
from kamiwaza_sdk.exceptions import KamiwazaError, NotFoundError
from kamiwaza_sdk.schemas.models.model import CreateModelConfig
from kamiwaza_sdk.utils.model_file_readiness import model_file_download_satisfied
from kamiwaza_sdk.utils.quant_manager import QuantizationManager
from model_targets import InferenceTarget, select_inference_target
from pydantic import ValidationError as SchemaValidationError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.slow,
]

_LANE_ENGINE = "llamacpp"
_READY_TIMEOUT_SECONDS = 900
_STOP_TIMEOUT_SECONDS = 300
_CHAT_TIMEOUT_SECONDS = 120.0
_POLL_ATTEMPTS = 15
_POLL_DELAY_SECONDS = 2.0

_T = TypeVar("_T")


def _wait_until(read: Callable[[], _T], done: Callable[[_T], bool], label: str) -> _T:
    """Poll ``read`` until ``done`` holds; running out of attempts fails the test."""
    last: _T | None = None
    for attempt in range(_POLL_ATTEMPTS):
        last = read()
        if done(last):
            return last
        if attempt < _POLL_ATTEMPTS - 1:
            time.sleep(_POLL_DELAY_SECONDS)
    raise AssertionError(
        f"{label}: not satisfied after {_POLL_ATTEMPTS} attempts; last={last!r}"
    )


@dataclass
class _Created:
    """Resources the test created and has not yet proven cleaned up.

    Deployments are registered only after their id is shown to be new, and
    configs with the unique name they were created under. Each leaves the
    registry only once its stop or deletion is confirmed.
    """

    deployment_ids: list[UUID] = field(default_factory=list)
    configs: dict[UUID, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _Lane:
    """What the test deploys: the target, its registered model and weights file."""

    target: InferenceTarget
    model: Any
    file_id: UUID


def _stop_and_wait(client, deployment_id: UUID, *, force: bool) -> None:
    """Stop a deployment and wait for STOPPED.

    The test body stops without force: on 1.2.1 a forced stop routes a failed
    stop to a cleanup that marks the row STOPPED and returns True, so only an
    unforced stop can show that stopping works. Teardown forces.
    """
    stopped = client.serving.stop_deployment(deployment_id=deployment_id, force=force)
    assert stopped is True, (
        f"stop_deployment({deployment_id}, force={force}) returned {stopped!r}"
    )
    client.serving.wait_for_deployment(
        deployment_id,
        desired_status=("STOPPED",),
        failure_status=("FAILED", "ERROR"),
        poll_interval=5.0,
        timeout=_STOP_TIMEOUT_SECONDS,
    )


def _cleanup_failures(client, registry: _Created) -> list[str]:
    """Stop and delete every registered resource, collecting failures instead of
    stopping at the first one. A config is deleted only if it still carries the
    name the test created it with, and its deletion is proven by NotFound."""
    failures = [
        _stop_failure(client, deployment_id)
        for deployment_id in reversed(registry.deployment_ids)
    ]
    failures += [
        _config_cleanup_failure(client, config_id, name)
        for config_id, name in reversed(list(registry.configs.items()))
    ]
    return [failure for failure in failures if failure]


def _stop_failure(client, deployment_id: UUID) -> str | None:
    try:
        _stop_and_wait(client, deployment_id, force=True)
    except (
        AssertionError,
        KamiwazaError,
        SchemaValidationError,
        TimeoutError,
    ) as exc:
        return f"could not stop deployment {deployment_id}: {exc!r}"
    return None


def _config_cleanup_failure(client, config_id: UUID, name: str) -> str | None:
    try:
        current = client.models.get_model_config(config_id)
    except NotFoundError as exc:
        # The body unregisters a config right after proving it deleted, so a
        # registered config should still be readable here. On 1.2.1 a refused
        # read also answers 404, so this cannot be taken as proof it is gone.
        return (
            f"could not confirm config {config_id} exists or was removed: "
            f"its cleanup read answered {exc!r}"
        )
    except (KamiwazaError, SchemaValidationError) as exc:
        return f"could not read config {config_id} before cleanup: {exc!r}"
    if current.name != name:
        return (
            f"refused to delete config {config_id}: it is named "
            f"{current.name!r}, not {name!r}"
        )
    try:
        client.models.delete_model_config(config_id)
    except NotFoundError as exc:
        # On 1.2.1 a delete refused by the permission check also answers 404,
        # so NotFound here is not proof of absence: the read-back decides, and
        # the 404 is kept for the failure message.
        return _config_absence_failure(
            client, config_id, f" (the delete answered {exc!r})"
        )
    except (KamiwazaError, SchemaValidationError) as exc:
        return f"could not delete config {config_id}: {exc!r}"
    return _config_absence_failure(client, config_id, "")


def _config_absence_failure(client, config_id: UUID, delete_answer: str) -> str | None:
    try:
        # The read masks a permission refusal as 404 too, but cleanup read the
        # config successfully just before deleting it, so NotFound means gone.
        client.models.get_model_config(config_id)
    except NotFoundError:
        return None
    except (KamiwazaError, SchemaValidationError) as exc:
        return f"could not confirm config {config_id} deleted: {exc!r}{delete_answer}"
    return f"config {config_id} still readable after delete{delete_answer}"


@pytest.fixture
def created(live_kamiwaza_client) -> Iterator[_Created]:
    """Clean up what the test body did not prove cleaned up.

    All failures are raised together, which pytest reports as a teardown error
    beside the test outcome rather than in place of it.
    """
    registry = _Created()
    yield registry
    failures = _cleanup_failures(live_kamiwaza_client, registry)
    if failures:
        raise AssertionError("model cleanup incomplete: " + "; ".join(failures))


def _lane_target() -> InferenceTarget:
    """The explicit fleet target if one is set, else the suite's GGUF target.

    Without a hardware snapshot, ``select_inference_target`` returns the explicit
    target when the fleet variables are set and ``GGUF_LLM_TARGET`` otherwise.
    """
    target = select_inference_target(None)
    if target.engine_name != _LANE_ENGINE:
        pytest.skip(
            f"not applicable: the explicit fleet target {target.repo_id} uses engine "
            f"{target.engine_name!r}; this test covers the {_LANE_ENGINE} lane"
        )
    return target


def _ready_lane(client, target: InferenceTarget, target_model_file_id) -> _Lane:
    """Return the target with its registered model and ready GGUF weights file.

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
    if not _weights_ready(model, file_id, target.quantization):
        _missing_prerequisite(
            target,
            f"prerequisite: GGUF weights for {target.repo_id} ({target.quantization}) "
            "are not all downloaded on this cluster; this test never downloads",
        )
    return _Lane(target=target, model=model, file_id=UUID(file_id))


def _weights_ready(model, file_id: str | None, quantization: str) -> bool:
    """A weights file was selected and every file of the quantization is ready.

    ``target_model_file_id`` picks one ready file, so a split GGUF with some
    shards still downloading would otherwise pass and fail at deploy time.
    """
    if model is None or file_id is None:
        return False
    return _all_target_files_ready(model, quantization)


def _all_target_files_ready(model, quantization: str) -> bool:
    """Every file of ``quantization`` is downloaded (split GGUF shards included).

    Mirrors the integration conftest's ``_model_has_ready_target_files``, which
    this module cannot import; a unit test pins the two to the same answers.
    """
    files = list(getattr(model, "m_files", None) or [])
    gguf_files = [
        f for f in files if str(getattr(f, "name", "") or "").lower().endswith(".gguf")
    ]
    target_files = files
    if gguf_files:
        target_files = QuantizationManager().filter_files_by_quantization(
            gguf_files, quantization, apply_fallback=False
        )
    if not target_files:
        return False
    return all(model_file_download_satisfied(f) for f in target_files)


def _missing_prerequisite(target: InferenceTarget, reason: str) -> NoReturn:
    """Fail for a fleet-required target, skip otherwise.

    Raises the exceptions ``pytest.fail`` / ``pytest.skip`` raise, so a type
    checker without pytest's stubs still sees that this never returns.
    """
    __tracebackhide__ = True
    if target.required:
        raise pytest.fail.Exception(msg=reason)
    raise pytest.skip.Exception(msg=reason)


def _config_template(client, lane: _Lane):
    """The model's default config (else its first), whose settings the test copies."""
    existing = client.models.get_model_configs(lane.model.id)
    if not existing:
        _missing_prerequisite(
            lane.target,
            f"prerequisite: {lane.model.repo_modelId} has no model config to copy settings from",
        )
    return next((c for c in existing if c.default), existing[0])


def _config_request(
    lane: _Lane, template, name: str, description: str
) -> CreateModelConfig:
    return CreateModelConfig(
        m_id=lane.model.id,
        m_file_id=lane.file_id,
        name=name,
        default=False,
        description=description,
        config=dict(template.config),
        system_config=dict(template.system_config),
    )


def _create_update_and_read_config(client, lane: _Lane, template, created: _Created):
    """Create a disposable config, prove it by reads and listings, update it, and
    prove the update by a read."""
    name = f"sdk-t09-{uuid4().hex[:8]}"
    description = "ENG-12327 T09 config"
    config = client.models.create_model_config(
        _config_request(lane, template, name, description)
    )
    created.configs[config.id] = name
    assert (config.m_id, config.name, config.default) == (lane.model.id, name, False)

    fetched = client.models.get_model_config(config.id)
    assert (fetched.name, fetched.m_file_id) == (name, lane.file_id)
    assert config.id in {c.id for c in client.models.get_model_configs(lane.model.id)}
    assert config.id in {
        c.id for c in client.models.get_model_configs_for_model(lane.model.id)
    }

    updated_description = f"{description} (updated)"
    updated = client.models.update_model_config(
        config.id, _config_request(lane, template, name, updated_description)
    )
    assert updated.id == config.id
    assert client.models.get_model_config(config.id).description == updated_description
    return config


def _deploy_fresh(client, lane: _Lane, config_id: UUID) -> UUID:
    """Deploy and return an id that did not exist anywhere before this call."""
    existing = {d.id for d in client.serving.list_deployments()}
    deployment_id = client.serving.deploy_model(
        model_id=lane.model.id,
        m_config_id=config_id,
        m_file_id=lane.file_id,
        engine_name=lane.target.engine_name,
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


def _deploy_and_check(client, lane: _Lane, config_id: UUID, created: _Created) -> UUID:
    """Deploy fresh to DEPLOYED and check the deployment carries what was requested
    and appears in the model's and the active listings."""
    deployment_id = _deploy_fresh(client, lane, config_id)
    created.deployment_ids.append(deployment_id)
    ready = client.serving.wait_deployment_ready(
        deployment_id, timeout_seconds=_READY_TIMEOUT_SECONDS
    )
    assert ready.status == "DEPLOYED"

    deployment = client.serving.get_deployment(deployment_id)
    assert (
        deployment.id,
        deployment.status,
        deployment.m_config_id,
        deployment.m_file_id,
        deployment.engine_name,
    ) == (deployment_id, "DEPLOYED", config_id, lane.file_id, lane.target.engine_name)
    assert deployment.instances, "a DEPLOYED deployment reports no instances"
    assert deployment_id in {
        d.id for d in client.serving.list_deployments(model_id=lane.model.id)
    }
    assert deployment_id in {d.id for d in client.serving.list_active_deployments()}
    return deployment_id


def _check_instances(client, deployment_id: UUID) -> None:
    instances = client.serving.list_model_instances(deployment_id)
    assert instances and all(i.deployment_id == deployment_id for i in instances)
    instance = client.serving.get_model_instance(instances[0].id)
    assert (instance.id, instance.deployment_id) == (instances[0].id, deployment_id)


def _sum_question() -> tuple[str, str]:
    """A prompt asking for the sum of two numbers chosen for this run, and the sum.

    The sum never appears in the prompt, so a reply that echoes the prompt or a
    fixed reply cannot contain it except by chance.
    """
    while True:
        left = 11 + secrets.randbelow(80)
        right = 11 + secrets.randbelow(80)
        answer = str(left + right)
        prompt = f"What is {left} plus {right}? Reply with only the number."
        if answer not in prompt:
            return prompt, answer


def _serve_one_chat(client, deployment_id: UUID) -> None:
    """One chat completion through the deployment; the reply must contain the sum."""
    prompt, answer = _sum_question()
    openai_client = client.openai.get_client(deployment_id=deployment_id)
    try:
        served = openai_client.models.list(timeout=_CHAT_TIMEOUT_SECONDS).data
        assert served, "the deployment's OpenAI-compatible endpoint lists no models"
        reply = openai_client.chat.completions.create(
            model=served[0].id,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=256,
            timeout=_CHAT_TIMEOUT_SECONDS,
        )
    finally:
        openai_client.close()

    assert reply.choices, "chat completion returned no choices"
    content = reply.choices[0].message.content or ""
    assert re.search(rf"(?<!\d){answer}(?!\d)", content), (
        f"reply to {prompt!r} does not contain the sum {answer}: {reply!r}"
    )
    assert reply.usage is not None and reply.usage.prompt_tokens > 0, reply
    assert reply.usage.completion_tokens > 0, reply


def _deployment_logs_once_captured(client, deployment_id: UUID):
    """Read captured logs, treating NotFound as capture not having started yet.

    The 1.2.1 route answers 404 while no log file exists for the deployment.
    """
    try:
        return client.serving.get_deployment_logs(deployment_id)
    except NotFoundError:
        return None


def _check_captured_and_streamed_logs(client, deployment_id: UUID) -> None:
    """Captured logs appear, and a streamed line is one of the captured lines."""
    logs = _wait_until(
        lambda: _deployment_logs_once_captured(client, deployment_id),
        lambda item: item is not None and bool(item.logs),
        "serving.get_deployment_logs for the new deployment",
    )
    assert logs.deployment_id == deployment_id
    streamed = list(
        itertools.islice(
            client.serving.stream_deployment_logs(
                deployment_id, poll_interval=1.0, max_empty_polls=3
            ),
            1,
        )
    )
    assert streamed and streamed[0] in logs.logs, (
        f"stream_deployment_logs did not yield a captured line: {streamed!r}"
    )


def _stop_and_prove_inactive(client, deployment_id: UUID, created: _Created) -> None:
    """Stop without force to STOPPED and prove the deployment left the active list."""
    _stop_and_wait(client, deployment_id, force=False)
    created.deployment_ids.remove(deployment_id)
    assert deployment_id not in {d.id for d in client.serving.list_active_deployments()}


def _delete_and_prove_config_gone(
    client, lane: _Lane, config_id: UUID, created: _Created
) -> None:
    """Delete the config, prove it NotFound, and prove it left the model's listing."""
    client.models.delete_model_config(config_id)
    with pytest.raises(NotFoundError):
        client.models.get_model_config(config_id)
    del created.configs[config_id]
    assert config_id not in {
        c.id for c in client.models.get_model_configs(lane.model.id)
    }


def test_model_config_and_local_deployment_lifecycle(
    live_kamiwaza_client,
    target_model_file_id,
    created,
) -> None:
    client = live_kamiwaza_client
    lane = _ready_lane(client, _lane_target(), target_model_file_id)
    template = _config_template(client, lane)
    config = _create_update_and_read_config(client, lane, template, created)
    deployment_id = _deploy_and_check(client, lane, config.id, created)
    _check_instances(client, deployment_id)
    _serve_one_chat(client, deployment_id)
    _check_captured_and_streamed_logs(client, deployment_id)
    _stop_and_prove_inactive(client, deployment_id, created)
    _delete_and_prove_config_gone(client, lane, config.id, created)
