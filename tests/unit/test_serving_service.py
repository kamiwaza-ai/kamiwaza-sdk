from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from kamiwaza_sdk.schemas.serving.serving import ContainerLogResponse
from kamiwaza_sdk.services.serving import (
    DeploymentLogStreamer,
    DeploymentStatusPoller,
    ServingService,
)


pytestmark = pytest.mark.unit


def test_deploy_model_builds_payload_with_repo_lookup(dummy_client):
    deployment_id = uuid4()
    responses = {("post", "/serving/deploy_model"): str(deployment_id)}
    client = dummy_client(responses)

    model_id = uuid4()
    config_id = uuid4()

    class DummyModels:
        def get_model_by_repo_id(self, repo_id):
            assert repo_id == "mlx-community/Qwen3-4B-4bit"
            return SimpleNamespace(id=model_id)

        def get_model_configs(self, mid):
            assert mid == model_id
            return [SimpleNamespace(id=config_id, default=True)]

    client.models = DummyModels()
    service = ServingService(client)

    result = service.deploy_model(repo_id="mlx-community/Qwen3-4B-4bit", lb_port=0, autoscaling=False)

    assert result == deployment_id
    method, path, payload = client.calls[0]
    assert (method, path) == ("post", "/serving/deploy_model")
    assert payload["json"]["m_id"] == str(model_id)
    assert payload["json"]["m_config_id"] == str(config_id)


class _StatusService:
    def __init__(self, statuses: list[str]):
        self.statuses = statuses
        self.calls = 0

    def get_deployment(self, deployment_id: UUID):
        idx = min(self.calls, len(self.statuses) - 1)
        status = self.statuses[idx]
        self.calls += 1
        return SimpleNamespace(status=status, id=deployment_id)


class _TimeStub:
    def __init__(self, step: float = 0.1):
        self.current = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.current
        self.current += self.step
        return value


def test_status_poller_returns_when_desired_status_reached():
    deployment_id = uuid4()
    service = _StatusService(["PENDING", "DEPLOYED"])
    sleep_calls: list[float] = []
    poller = DeploymentStatusPoller(
        service,
        poll_interval=1.0,
        timeout=10.0,
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
        time_fn=_TimeStub(),
    )

    deployment = poller.wait_for(deployment_id, desired_status=["DEPLOYED"], failure_status=["FAILED"])

    assert deployment.status == "DEPLOYED"
    assert sleep_calls == [1.0]


def test_status_poller_raises_on_failure_status():
    deployment_id = uuid4()
    service = _StatusService(["PENDING", "FAILED"])
    poller = DeploymentStatusPoller(
        service,
        poll_interval=0,
        timeout=5.0,
        sleep_fn=lambda _: None,
        time_fn=_TimeStub(),
    )

    with pytest.raises(RuntimeError):
        poller.wait_for(deployment_id, desired_status=["DEPLOYED"], failure_status=["FAILED"])


def test_status_poller_times_out_when_threshold_exceeded():
    deployment_id = uuid4()
    service = _StatusService(["PENDING"])
    poller = DeploymentStatusPoller(
        service,
        poll_interval=0,
        timeout=0.15,
        sleep_fn=lambda _: None,
        time_fn=_TimeStub(step=0.2),
    )

    with pytest.raises(TimeoutError):
        poller.wait_for(deployment_id, desired_status=["DEPLOYED"], failure_status=["FAILED"])


def _log_response(deployment_id: UUID, lines: list[str], capture_active: bool) -> ContainerLogResponse:
    return ContainerLogResponse(
        deployment_id=deployment_id,
        engine_type="llamacpp",
        container_id=None,
        log_file_path="/var/log/deploy.log",
        logs=lines,
        total_lines_seen=len(lines),
        current_lines_stored=len(lines),
        compressed=False,
        capture_active=capture_active,
    )


class _LogService:
    def __init__(self, responses: list[ContainerLogResponse]):
        self._responses = responses
        self.calls = 0

    def get_deployment_logs(self, deployment_id: UUID) -> ContainerLogResponse:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]


def test_log_streamer_yields_new_lines_until_capture_stops():
    deployment_id = uuid4()
    responses = [
        _log_response(deployment_id, ["init"], True),
        _log_response(deployment_id, ["init", "ready"], False),
        _log_response(deployment_id, ["init", "ready"], False),
    ]
    service = _LogService(responses)
    sleeps: list[float] = []
    streamer = DeploymentLogStreamer(service, poll_interval=0.5, sleep_fn=lambda seconds: sleeps.append(seconds))

    lines = list(streamer.stream(deployment_id))

    assert lines == ["init", "ready"]
    assert sleeps == [0.5, 0.5]


def test_log_streamer_respects_custom_stop_condition():
    deployment_id = uuid4()
    responses = [
        _log_response(deployment_id, ["boot"], True),
        _log_response(deployment_id, ["boot", "warmup"], True),
    ]
    service = _LogService(responses)
    streamer = DeploymentLogStreamer(service, poll_interval=0, sleep_fn=lambda _: None)

    stop_after_two = lambda resp: resp.total_lines_seen >= 2
    lines = list(
        streamer.stream(
            deployment_id,
            stop_when=stop_after_two,
            max_empty_polls=1,
        )
    )

    assert lines == ["boot", "warmup"]
