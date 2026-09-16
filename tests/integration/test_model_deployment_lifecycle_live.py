"""Model configuration and deployment lifecycle through the SDK (ENG-12327, T09).

Both tests deploy the suite's hardware-appropriate target (on a GPU-less host,
the llama.cpp GGUF target) using weights that are ALREADY on the cluster. They
never download: ``initiate_model_download`` and ``wait_for_download`` are
replaced with a function that fails the test, and a target whose weights are not
ready is skipped before anything is created.

The two capabilities are evidenced by separate tests so that a failure in one
cannot mark the other as failing:

* ``test_model_config_and_local_deployment_lifecycle`` -- a disposable model
  config is created, read, listed and updated; a deployment using it reaches
  DEPLOYED and exposes its instances and logs; it is stopped, and the config is
  deleted and proven absent.
* ``test_openai_compatible_inference_through_sdk_client`` -- one real chat
  completion through the OpenAI-compatible client the SDK hands out for a
  deployment.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, NoReturn, TypeVar
from uuid import UUID, uuid4

import pytest
from kamiwaza_sdk.exceptions import NotFoundError
from kamiwaza_sdk.schemas.models.model import CreateModelConfig

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
_PROMPT = [{"role": "user", "content": "Reply with a single short greeting."}]

_T = TypeVar("_T")


def _refuse_download(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise AssertionError(
        "T09 must never download model weights; the target must already be present"
    )


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
    """Deployments and configs a test created and has not cleaned up itself."""

    deployment_ids: list[UUID] = field(default_factory=list)
    config_ids: list[UUID] = field(default_factory=list)


@pytest.fixture
def created(live_kamiwaza_client) -> Iterator[_Created]:
    """Stop and delete whatever the test body left behind.

    A cleanup error here is reported by pytest as a teardown error beside the
    test outcome, never in place of it.
    """
    registry = _Created()
    yield registry
    client = live_kamiwaza_client
    for deployment_id in reversed(registry.deployment_ids):
        client.serving.stop_deployment(deployment_id=deployment_id, force=True)
    for config_id in reversed(registry.config_ids):
        client.models.delete_model_config(config_id)


@pytest.fixture
def no_downloads(live_kamiwaza_client, monkeypatch) -> None:
    """Make any attempt to download model weights fail the test."""
    models = live_kamiwaza_client.models
    monkeypatch.setattr(models, "initiate_model_download", _refuse_download)
    monkeypatch.setattr(models, "wait_for_download", _refuse_download)
    assert models.initiate_model_download is _refuse_download
    assert models.wait_for_download is _refuse_download


def _ready_target(client, target, target_model_file_id) -> tuple[Any, UUID]:
    """Return the target model and its ready weights file, or skip.

    Runs before anything is created, so a skip here never hides a product
    failure. A fleet-required target that is not ready fails instead.
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
        reason = (
            f"prerequisite: {target.repo_id} ({target.quantization}) is not already "
            "downloaded on this cluster; this test never downloads"
        )
        if target.required:
            pytest.fail(reason)
        pytest.skip(reason)
    return model, UUID(file_id)


def _deploy(client, model, config_id: UUID, file_id: UUID, engine_name: str) -> UUID:
    deployment_id = client.serving.deploy_model(
        model_id=model.id,
        m_config_id=config_id,
        m_file_id=file_id,
        engine_name=engine_name,
        lb_port=0,
        autoscaling=False,
        min_copies=1,
        starting_copies=1,
        wait=False,
    )
    assert isinstance(deployment_id, UUID), f"deploy_model refused: {deployment_id!r}"
    return deployment_id


@pytest.mark.usefixtures("no_downloads")
def test_model_config_and_local_deployment_lifecycle(
    live_kamiwaza_client,
    deployable_model_target,
    target_model_file_id,
    created,
) -> None:
    client = live_kamiwaza_client
    target = deployable_model_target
    model, file_id = _ready_target(client, target, target_model_file_id)

    existing = client.models.get_model_configs(model.id)
    if not existing:
        pytest.skip(
            f"prerequisite: {target.repo_id} has no model config to copy settings from"
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
    created.config_ids.append(config.id)
    assert (config.m_id, config.name, config.default) == (model.id, name, False)

    assert client.models.get_model_config(config.id).name == name
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

    deployment_id = _deploy(client, model, config.id, file_id, target.engine_name)
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
    active = next(
        (d for d in client.serving.list_active_deployments() if d.id == deployment_id),
        None,
    )
    assert active is not None and active.endpoint, "deployment missing from active list"

    instances = client.serving.list_model_instances(deployment_id)
    assert instances and all(i.deployment_id == deployment_id for i in instances)
    instance = client.serving.get_model_instance(instances[0].id)
    assert (instance.id, instance.deployment_id) == (instances[0].id, deployment_id)

    logs = _wait_until(
        lambda: client.serving.get_deployment_logs(deployment_id),
        lambda item: bool(item.logs),
        "serving.get_deployment_logs for the new deployment",
    )
    assert logs.deployment_id == deployment_id
    patterns = client.serving.get_deployment_log_patterns(deployment_id)
    assert patterns.deployment_id == deployment_id
    streamed = list(
        itertools.islice(
            client.serving.stream_deployment_logs(
                deployment_id, poll_interval=1.0, max_empty_polls=3
            ),
            1,
        )
    )
    assert streamed, "serving.stream_deployment_logs yielded no lines"

    assert client.serving.stop_deployment(deployment_id=deployment_id) is True
    created.deployment_ids.remove(deployment_id)
    stopped = client.serving.wait_for_deployment(
        deployment_id,
        desired_status=("STOPPED",),
        failure_status=("FAILED", "ERROR"),
        poll_interval=5.0,
        timeout=_STOP_TIMEOUT_SECONDS,
    )
    assert stopped.status == "STOPPED"
    assert deployment_id not in {d.id for d in client.serving.list_active_deployments()}

    client.models.delete_model_config(config.id)
    created.config_ids.remove(config.id)
    with pytest.raises(NotFoundError):
        client.models.get_model_config(config.id)
    assert config.id not in {c.id for c in client.models.get_model_configs(model.id)}


@pytest.mark.usefixtures("no_downloads")
def test_openai_compatible_inference_through_sdk_client(
    live_kamiwaza_client,
    deployable_model_target,
    target_model_file_id,
    created,
) -> None:
    client = live_kamiwaza_client
    target = deployable_model_target
    model, file_id = _ready_target(client, target, target_model_file_id)

    configs = client.models.get_model_configs(model.id)
    if not configs:
        pytest.skip(
            f"prerequisite: {target.repo_id} has no model config to deploy with"
        )
    config = next((c for c in configs if c.default), configs[0])

    deployment_id = _deploy(client, model, config.id, file_id, target.engine_name)
    created.deployment_ids.append(deployment_id)
    client.serving.wait_deployment_ready(
        deployment_id, timeout_seconds=_READY_TIMEOUT_SECONDS
    )

    openai_client = client.openai.get_client(deployment_id=deployment_id)
    served = openai_client.models.list().data
    assert served, "the deployment's OpenAI-compatible endpoint lists no models"
    reply = openai_client.chat.completions.create(
        model=served[0].id,
        messages=_PROMPT,
        temperature=0.0,
        max_tokens=32,
    )
    assert reply.choices, "chat completion returned no choices"
    content = reply.choices[0].message.content
    assert content and content.strip(), (
        f"chat completion returned empty content: {reply!r}"
    )
