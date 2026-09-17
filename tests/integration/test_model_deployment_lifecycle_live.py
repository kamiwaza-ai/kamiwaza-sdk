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
transcription and image generation), which the capability map enforces since
ENG-12269.

Deliberately not called: the log-pattern route, which on 1.2.1 reads only a
local log file or Kubernetes pod logs and, unlike the captured-log route, has no
host-spawner source, so it answers 404 for a deployment whose logs only the host
spawner holds. And ``serving.get_health``: on 1.2.1 it returns entries for
deployments still INITIALIZING or whose check finds a problem, but none for a
DEPLOYED local-engine deployment whose Ray Serve route is present, so no
assertion about this test's deployment could fail for a broken health check.
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
    failures: list[str] = []
    for deployment_id in reversed(registry.deployment_ids):
        try:
            _stop_and_wait(client, deployment_id, force=True)
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


def _ready_target(
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
        _missing_prerequisite(
            target,
            f"prerequisite: GGUF weights for {target.repo_id} ({target.quantization}) "
            "are not already on this cluster; this test never downloads",
        )
    return model, UUID(file_id)


def _missing_prerequisite(target: InferenceTarget, reason: str) -> NoReturn:
    """Fail for a fleet-required target, skip otherwise.

    Raises the exceptions ``pytest.fail`` / ``pytest.skip`` raise, so a type
    checker without pytest's stubs still sees that this never returns.
    """
    __tracebackhide__ = True
    if target.required:
        raise pytest.fail.Exception(msg=reason)
    raise pytest.skip.Exception(msg=reason)


def _deploy_fresh(
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


def _assert_deployed_as_requested(
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


def _deployment_logs_once_captured(client, deployment_id: UUID):
    """Read captured logs, treating NotFound as capture not having started yet.

    The 1.2.1 route answers 404 while no log file exists for the deployment.
    """
    try:
        return client.serving.get_deployment_logs(deployment_id)
    except NotFoundError:
        return None


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


def test_model_config_and_local_deployment_lifecycle(
    live_kamiwaza_client,
    target_model_file_id,
    created,
) -> None:
    client = live_kamiwaza_client
    target = _lane_target()
    model, file_id = _ready_target(client, target, target_model_file_id)

    existing = client.models.get_model_configs(model.id)
    if not existing:
        _missing_prerequisite(
            target,
            f"prerequisite: {model.repo_modelId} has no model config to copy settings from",
        )
    template = next((c for c in existing if c.default), existing[0])

    name = f"sdk-t09-{uuid4().hex[:8]}"
    description = "ENG-12327 T09 config"
    config = client.models.create_model_config(
        CreateModelConfig(
            m_id=model.id,
            m_file_id=file_id,
            name=name,
            default=False,
            description=description,
            config=dict(template.config),
            system_config=dict(template.system_config),
        )
    )
    created.configs[config.id] = name
    assert (config.m_id, config.name, config.default) == (model.id, name, False)

    fetched = client.models.get_model_config(config.id)
    assert (fetched.name, fetched.m_file_id) == (name, file_id)
    assert config.id in {c.id for c in client.models.get_model_configs(model.id)}
    assert config.id in {
        c.id for c in client.models.get_model_configs_for_model(model.id)
    }

    updated_description = f"{description} (updated)"
    updated = client.models.update_model_config(
        config.id,
        CreateModelConfig(
            m_id=model.id,
            m_file_id=file_id,
            name=name,
            default=False,
            description=updated_description,
            config=dict(template.config),
            system_config=dict(template.system_config),
        ),
    )
    assert updated.id == config.id
    assert client.models.get_model_config(config.id).description == updated_description

    deployment_id = _deploy_fresh(client, target, model, config.id, file_id)
    created.deployment_ids.append(deployment_id)
    ready = client.serving.wait_deployment_ready(
        deployment_id, timeout_seconds=_READY_TIMEOUT_SECONDS
    )
    assert ready.status == "DEPLOYED"

    deployment = client.serving.get_deployment(deployment_id)
    _assert_deployed_as_requested(deployment, deployment_id, target, config.id, file_id)
    assert deployment.instances, "a DEPLOYED deployment reports no instances"
    assert deployment_id in {
        d.id for d in client.serving.list_deployments(model_id=model.id)
    }
    assert deployment_id in {d.id for d in client.serving.list_active_deployments()}

    instances = client.serving.list_model_instances(deployment_id)
    assert instances and all(i.deployment_id == deployment_id for i in instances)
    instance = client.serving.get_model_instance(instances[0].id)
    assert (instance.id, instance.deployment_id) == (instances[0].id, deployment_id)

    prompt, answer = _sum_question()
    openai_client = client.openai.get_client(deployment_id=deployment_id)
    try:
        served = openai_client.models.list().data
        assert served, "the deployment's OpenAI-compatible endpoint lists no models"
        reply = openai_client.chat.completions.create(
            model=served[0].id,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=16,
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

    _stop_and_wait(client, deployment_id, force=False)
    created.deployment_ids.remove(deployment_id)
    assert deployment_id not in {d.id for d in client.serving.list_active_deployments()}

    client.models.delete_model_config(config.id)
    with pytest.raises(NotFoundError):
        client.models.get_model_config(config.id)
    del created.configs[config.id]
    assert config.id not in {c.id for c in client.models.get_model_configs(model.id)}
