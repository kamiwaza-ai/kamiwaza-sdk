"""Model configuration, local deployment and inference through the SDK (ENG-12327, T09).

Both tests deploy the suite's llama.cpp GGUF target (``GGUF_LLM_TARGET``), the
CPU-capable lane ENG-12327 chose for 1.2.1, from weights already on the cluster.
Before creating anything each test confirms a ready GGUF file exists and skips
otherwise; neither test calls an SDK download method.

Each test carries one capability, so an inference failure cannot mark local
deployment as failing. The inference test has to deploy before it can infer, so
a deployment failure fails both records.

* ``test_model_config_and_local_deployment_lifecycle`` -- a disposable model
  config is created, read, listed and updated; a fresh deployment using it
  reaches DEPLOYED and is checked through the deployment, active-deployment,
  instance, captured-log, log-pattern and log-stream methods; it is stopped to
  STOPPED, and the config is deleted and proven NotFound.
* ``test_openai_compatible_inference_through_sdk_client`` -- one chat completion,
  through the OpenAI-compatible client the SDK returns for a fresh deployment,
  whose reply must contain a sentinel word chosen for this run.
"""

from __future__ import annotations

import itertools
import secrets
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import UUID, uuid4

import pytest
import requests
from kamiwaza_sdk.exceptions import KamiwazaError, NotFoundError
from kamiwaza_sdk.schemas.models.model import CreateModelConfig
from model_targets import GGUF_LLM_TARGET

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.slow,
]

_READY_TIMEOUT_SECONDS = 900
_STOP_TIMEOUT_SECONDS = 300
_POLL_ATTEMPTS = 15
_POLL_DELAY_SECONDS = 2.0
# One is chosen per run, so no fixed reply can contain the requested word every time.
_SENTINEL_WORDS = ("violet", "harbor", "copper", "meadow", "lantern", "orchard")

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
    """Resources a test created and has not yet proven cleaned up.

    Deployments are registered only after their id is shown to be new, and
    configs with the unique name they were created under. Each leaves the
    registry only once its stop or deletion is confirmed.
    """

    deployment_ids: list[UUID] = field(default_factory=list)
    configs: dict[UUID, str] = field(default_factory=dict)


def _stop_and_wait(client, deployment_id: UUID) -> None:
    assert (
        client.serving.stop_deployment(deployment_id=deployment_id, force=True) is True
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
    stopping at the first one."""
    failures: list[str] = []
    for deployment_id in reversed(registry.deployment_ids):
        try:
            _stop_and_wait(client, deployment_id)
        except (
            AssertionError,
            KamiwazaError,
            TimeoutError,
            requests.RequestException,
        ) as exc:
            failures.append(f"could not stop deployment {deployment_id}: {exc!r}")
    for config_id, name in reversed(list(registry.configs.items())):
        try:
            current = client.models.get_model_config(config_id)
        except NotFoundError:
            continue
        except (KamiwazaError, requests.RequestException) as exc:
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
        try:
            client.models.delete_model_config(config_id)
        except (KamiwazaError, requests.RequestException) as exc:
            failures.append(f"could not delete config {config_id}: {exc!r}")
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


def _ready_gguf_target(client, target_model_file_id) -> tuple[Any, UUID]:
    """Return the GGUF target model and its ready weights file, or skip.

    Runs before anything is created. It cannot tell an absent prerequisite from
    a listing that wrongly returns nothing, so a skip is a missing prerequisite
    only as far as the model and file listings can show.
    """
    target = GGUF_LLM_TARGET
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
        pytest.skip(
            f"prerequisite: GGUF weights for {target.repo_id} ({target.quantization}) "
            "are not already on this cluster; this test never downloads"
        )
    return model, UUID(file_id)


def _deploy_fresh(client, model, config_id: UUID, file_id: UUID) -> UUID:
    """Deploy and return an id that did not exist before this call."""
    existing = {d.id for d in client.serving.list_deployments(model_id=model.id)}
    deployment_id = client.serving.deploy_model(
        model_id=model.id,
        m_config_id=config_id,
        m_file_id=file_id,
        engine_name=GGUF_LLM_TARGET.engine_name,
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


def _deployment_logs_once_captured(client, deployment_id: UUID):
    """Read captured logs, treating NotFound as capture not having started yet.

    The 1.2.1 route answers 404 while no log file exists for the deployment.
    """
    try:
        return client.serving.get_deployment_logs(deployment_id)
    except NotFoundError:
        return None


def test_model_config_and_local_deployment_lifecycle(
    live_kamiwaza_client,
    target_model_file_id,
    created,
) -> None:
    client = live_kamiwaza_client
    model, file_id = _ready_gguf_target(client, target_model_file_id)

    existing = client.models.get_model_configs(model.id)
    if not existing:
        pytest.skip(
            f"prerequisite: {model.repo_modelId} has no model config to copy settings from"
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

    deployment_id = _deploy_fresh(client, model, config.id, file_id)
    created.deployment_ids.append(deployment_id)
    ready = client.serving.wait_deployment_ready(
        deployment_id, timeout_seconds=_READY_TIMEOUT_SECONDS
    )
    assert ready.status == "DEPLOYED"

    deployment = client.serving.get_deployment(deployment_id)
    assert (deployment.id, deployment.m_config_id, deployment.status) == (
        deployment_id,
        config.id,
        "DEPLOYED",
    )
    assert deployment.instances, "a DEPLOYED deployment reports no instances"
    assert deployment_id in {
        d.id for d in client.serving.list_deployments(model_id=model.id)
    }
    assert deployment_id in {d.id for d in client.serving.list_active_deployments()}

    instances = client.serving.list_model_instances(deployment_id)
    assert instances and all(i.deployment_id == deployment_id for i in instances)
    instance = client.serving.get_model_instance(instances[0].id)
    assert (instance.id, instance.deployment_id) == (instances[0].id, deployment_id)

    logs = _wait_until(
        lambda: _deployment_logs_once_captured(client, deployment_id),
        lambda item: item is not None and bool(item.logs),
        "serving.get_deployment_logs for the new deployment",
    )
    assert logs.deployment_id == deployment_id
    patterns = client.serving.get_deployment_log_patterns(deployment_id)
    assert patterns.patterns_detected, "log pattern analysis returned no detectors"
    assert patterns.patterns_detected.get("model_loading_failure") is False, (
        f"a DEPLOYED, serving model was flagged as failing to load: {patterns!r}"
    )
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

    _stop_and_wait(client, deployment_id)
    created.deployment_ids.remove(deployment_id)
    assert deployment_id not in {d.id for d in client.serving.list_active_deployments()}

    client.models.delete_model_config(config.id)
    with pytest.raises(NotFoundError):
        client.models.get_model_config(config.id)
    del created.configs[config.id]
    assert config.id not in {c.id for c in client.models.get_model_configs(model.id)}


def test_openai_compatible_inference_through_sdk_client(
    live_kamiwaza_client,
    target_model_file_id,
    created,
) -> None:
    client = live_kamiwaza_client
    model, file_id = _ready_gguf_target(client, target_model_file_id)

    configs = client.models.get_model_configs(model.id)
    if not configs:
        pytest.skip(
            f"prerequisite: {model.repo_modelId} has no model config to deploy with"
        )
    config = next((c for c in configs if c.default), configs[0])

    deployment_id = _deploy_fresh(client, model, config.id, file_id)
    created.deployment_ids.append(deployment_id)
    client.serving.wait_deployment_ready(
        deployment_id, timeout_seconds=_READY_TIMEOUT_SECONDS
    )

    sentinel = secrets.choice(_SENTINEL_WORDS)
    openai_client = client.openai.get_client(deployment_id=deployment_id)
    try:
        served = openai_client.models.list().data
        assert served, "the deployment's OpenAI-compatible endpoint lists no models"
        reply = openai_client.chat.completions.create(
            model=served[0].id,
            messages=[
                {
                    "role": "user",
                    "content": f"Reply with exactly one word, in lowercase: {sentinel}",
                }
            ],
            temperature=0.0,
            max_tokens=16,
        )
    finally:
        openai_client.close()

    assert reply.choices, "chat completion returned no choices"
    content = reply.choices[0].message.content or ""
    assert sentinel in content.lower(), (
        f"reply does not contain the requested word {sentinel!r}: {reply!r}"
    )
    assert reply.usage is not None and reply.usage.prompt_tokens > 0, reply
    assert reply.usage.completion_tokens > 0, reply
