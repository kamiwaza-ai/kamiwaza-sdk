"""Model configuration and local deployment lifecycle through the SDK (ENG-12327, T09).

Target selection and the prerequisite rule are shared with
``test_openai_compatible_inference_live.py`` and described in
``model_deployment_live_support``.

A disposable model config is created, read, listed and updated; a fresh
deployment using it reaches DEPLOYED with the requested engine and weights file
and is checked through the deployment, active-deployment, instance, captured-log
and log-stream methods; it is stopped (without force) to STOPPED, and the config
is deleted and proven NotFound.

The log-pattern route is deliberately not called. On 1.2.1 it reads only a
local log file or Kubernetes pod logs and, unlike the captured-log route, has no
host-spawner source, so it answers 404 for a deployment whose logs only the host
spawner holds. That would fail this capability for a reason unrelated to
deployment.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable, Iterator
from typing import TypeVar
from uuid import UUID, uuid4

import pytest
from kamiwaza_sdk.exceptions import NotFoundError
from kamiwaza_sdk.schemas.models.model import CreateModelConfig
from tests.integration.model_deployment_live_support import (
    READY_TIMEOUT_SECONDS,
    DeploymentRegistry,
    assert_deployed_as_requested,
    deploy_fresh,
    lane_target,
    missing_prerequisite,
    ready_target,
    registry_with_cleanup,
    stop_and_wait,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.slow,
]

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


@pytest.fixture
def created(live_kamiwaza_client) -> Iterator[DeploymentRegistry]:
    """Clean up what the test body did not prove cleaned up."""
    yield from registry_with_cleanup(live_kamiwaza_client)


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
    target = lane_target()
    model, file_id = ready_target(client, target, target_model_file_id)

    existing = client.models.get_model_configs(model.id)
    if not existing:
        missing_prerequisite(
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

    deployment_id = deploy_fresh(client, target, model, config.id, file_id)
    created.deployment_ids.append(deployment_id)
    ready = client.serving.wait_deployment_ready(
        deployment_id, timeout_seconds=READY_TIMEOUT_SECONDS
    )
    assert ready.status == "DEPLOYED"

    deployment = client.serving.get_deployment(deployment_id)
    assert_deployed_as_requested(deployment, deployment_id, target, config.id, file_id)
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

    stop_and_wait(client, deployment_id, force=False)
    created.deployment_ids.remove(deployment_id)
    assert deployment_id not in {d.id for d in client.serving.list_active_deployments()}

    client.models.delete_model_config(config.id)
    with pytest.raises(NotFoundError):
        client.models.get_model_config(config.id)
    del created.configs[config.id]
    assert config.id not in {c.id for c in client.models.get_model_configs(model.id)}
