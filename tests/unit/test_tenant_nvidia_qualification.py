"""ENG-11513: a selected NVIDIA qualification lane must fail honestly."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

pytestmark = pytest.mark.unit

DEPLOYMENT_ID = UUID("00000000-0000-0000-0000-000000000004")
REQUEST = {
    "m_id": "00000000-0000-0000-0000-000000000001",
    "m_config_id": "00000000-0000-0000-0000-000000000002",
    "m_file_id": "00000000-0000-0000-0000-000000000003",
    "engine_name": "llamacpp",
    "inferenceResources": {
        "schemaVersion": 1,
        "accelerator": {
            "capability": "gpu",
            "count": 1,
            "memory": {"minimum": "4Gi"},
            "isolation": "any-qualified",
            "profile": "nvidia-whole",
        },
        "runtime": {"selection": "automatic"},
        "alternatives": [],
    },
}


@pytest.fixture(scope="module")
def lane():
    path = Path(__file__).parents[1] / "integration/test_tenant_nvidia_live.py"
    spec = importlib.util.spec_from_file_location("tenant_nvidia_lane", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def target(lane):
    return lane.load_request(json.dumps(REQUEST))


@pytest.fixture
def client(target):
    result = Mock()
    result.serving.deploy_model.return_value = DEPLOYMENT_ID
    result.serving.wait_deployment_ready.return_value = SimpleNamespace(
        status="DEPLOYED", instances=[object()]
    )
    result.serving.get_deployment.side_effect = [
        SimpleNamespace(inference_resources=target.inference_resources),
        SimpleNamespace(status="STOPPED", instances=[]),
    ]
    result.serving.stop_deployment.return_value = True
    inference = result.openai.get_client.return_value.with_options.return_value
    inference.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ready"))]
    )
    return result


def test_unconfigured_lane_skips_without_client(lane, monkeypatch):
    monkeypatch.delenv("KAMIWAZA_TENANT_NVIDIA_REQUEST", raising=False)
    request = Mock()
    with pytest.raises(pytest.skip.Exception):
        lane.test_explicit_nvidia_owner_lifecycle(request, Mock())
    request.getfixturevalue.assert_not_called()


@pytest.mark.parametrize("value", ["{}", "not json"])
def test_invalid_config_fails(lane, value):
    with pytest.raises(ValueError):
        lane.load_request(value)


@pytest.mark.parametrize("field", ["m_file_id", "inferenceResources"])
def test_missing_owner_selection_fails(lane, field):
    value = dict(REQUEST)
    value.pop(field)
    with pytest.raises(AssertionError):
        lane.load_request(json.dumps(value))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("engine_name", "vllm"),
        ("inferenceResources", {"schemaVersion": 1, "alternatives": []}),
    ],
)
def test_nvidia_lane_rejects_non_qualified_target(lane, field, value):
    request = json.loads(json.dumps(REQUEST))
    request[field] = value
    with pytest.raises((ValueError, AssertionError)):
        lane.load_request(json.dumps(request))


def test_nvidia_lane_rejects_cpu_fallback(lane):
    request = json.loads(json.dumps(REQUEST))
    request["inferenceResources"]["alternatives"] = [{"capability": "cpu", "count": 1}]
    with pytest.raises((ValueError, AssertionError)):
        lane.load_request(json.dumps(request))


def test_success_pins_request_and_checks_cold_warm_stop(lane, client, target):
    record = Mock()
    lane.qualify(client, target, record)
    arguments = client.serving.deploy_model.call_args.kwargs
    assert arguments["m_file_id"] == target.m_file_id
    assert arguments["inferenceResources"] == REQUEST["inferenceResources"]
    assert arguments["wait"] is False
    client.serving.wait_deployment_ready.assert_called_once_with(
        DEPLOYMENT_ID, timeout_seconds=600, poll_interval_seconds=5
    )
    inference = client.openai.get_client.return_value.with_options.return_value
    client.openai.get_client.assert_called_once_with(deployment_id=DEPLOYMENT_ID)
    client.openai.get_client.return_value.with_options.assert_called_once_with(
        timeout=60, max_retries=0
    )
    assert inference.chat.completions.create.call_count == 2
    inference.close.assert_called_once()
    client.serving.stop_deployment.assert_called_once_with(
        deployment_id=DEPLOYMENT_ID, force=True
    )
    record.assert_any_call("deployment_id", str(DEPLOYMENT_ID))


@pytest.mark.parametrize("step", ["wait_deployment_ready", "get_deployment"])
def test_readiness_and_readback_failures_propagate_and_stop(lane, client, target, step):
    error = RuntimeError("selected lane failed")
    getattr(client.serving, step).side_effect = [
        error,
        SimpleNamespace(status="STOPPED", instances=[]),
    ]
    if step == "wait_deployment_ready":
        client.serving.get_deployment.side_effect = [
            SimpleNamespace(status="STOPPED", instances=[])
        ]
    with pytest.raises(RuntimeError, match="selected lane failed"):
        lane.qualify(client, target, Mock())
    client.serving.stop_deployment.assert_called_once()


def test_empty_chat_fails_and_stops(lane, client, target):
    inference = client.openai.get_client.return_value.with_options.return_value
    inference.chat.completions.create.return_value.choices = []
    with pytest.raises(AssertionError):
        lane.qualify(client, target, Mock())
    inference.close.assert_called_once()
    client.serving.stop_deployment.assert_called_once()


def test_missing_resource_readback_fails_and_stops(lane, client, target):
    client.serving.get_deployment.side_effect = [
        SimpleNamespace(inference_resources=None),
        SimpleNamespace(status="STOPPED", instances=[]),
    ]
    with pytest.raises(AssertionError, match="resource"):
        lane.qualify(client, target, Mock())
    client.serving.stop_deployment.assert_called_once()


def test_stop_refusal_is_failure(lane, client, target):
    client.serving.stop_deployment.return_value = False
    with pytest.raises(AssertionError, match="stop"):
        lane.qualify(client, target, Mock())


def test_stop_residue_reaches_bounded_failure(lane, client, monkeypatch):
    client.serving.get_deployment.side_effect = None
    client.serving.get_deployment.return_value = SimpleNamespace(
        status="STOPPED", instances=[object()]
    )
    monkeypatch.setattr(lane.time, "monotonic", Mock(side_effect=[0, 91]))
    with pytest.raises(TimeoutError, match="stop"):
        lane.stop_and_verify(client, DEPLOYMENT_ID)


def test_configured_lane_converts_client_skip_to_failure(lane, monkeypatch):
    monkeypatch.setenv("KAMIWAZA_TENANT_NVIDIA_REQUEST", json.dumps(REQUEST))
    request = Mock()
    request.getfixturevalue.side_effect = pytest.skip.Exception("missing auth")
    with pytest.raises(pytest.fail.Exception, match="configured NVIDIA"):
        lane.test_explicit_nvidia_owner_lifecycle(request, Mock())


@pytest.mark.parametrize("step", ["deploy_model", "stop_deployment"])
def test_api_errors_are_not_converted_to_skip(lane, client, target, step):
    from kamiwaza_sdk.exceptions import APIError

    getattr(client.serving, step).side_effect = APIError("unavailable", status_code=503)
    with pytest.raises(APIError):
        lane.qualify(client, target, Mock())


def test_refused_deploy_fails_without_stopping_unowned_id(lane, client, target):
    client.serving.deploy_model.return_value = False
    with pytest.raises(AssertionError, match="id"):
        lane.qualify(client, target, Mock())
    client.serving.stop_deployment.assert_not_called()


def test_invoke_exception_still_closes_and_stops(lane, client, target):
    inference = client.openai.get_client.return_value.with_options.return_value
    inference.chat.completions.create.side_effect = TimeoutError("invoke timed out")
    with pytest.raises(TimeoutError, match="invoke timed out"):
        lane.qualify(client, target, Mock())
    inference.close.assert_called_once()
    client.serving.stop_deployment.assert_called_once()


def test_stop_waits_for_eventual_api_absence(lane, client, monkeypatch):
    client.serving.get_deployment.side_effect = [
        SimpleNamespace(status="STOPPING", instances=[object()]),
        SimpleNamespace(status="STOPPED", instances=[]),
    ]
    monkeypatch.setattr(lane.time, "sleep", Mock())
    lane.stop_and_verify(client, DEPLOYMENT_ID)
    lane.time.sleep.assert_called_once_with(1)


@pytest.mark.parametrize("profile", [None, ""])
def test_profile_is_required(lane, profile):
    value = json.loads(json.dumps(REQUEST))
    value["inferenceResources"]["accelerator"]["profile"] = profile
    with pytest.raises((ValueError, AssertionError)):
        lane.load_request(json.dumps(value))


def test_configured_lane_runs_the_qualified_target(lane, client, monkeypatch):
    monkeypatch.setenv("KAMIWAZA_TENANT_NVIDIA_REQUEST", json.dumps(REQUEST))
    request = Mock()
    request.getfixturevalue.return_value = client
    lane.test_explicit_nvidia_owner_lifecycle(request, Mock())
    request.getfixturevalue.assert_called_once_with("live_kamiwaza_client")
    client.serving.stop_deployment.assert_called_once()


def test_evidence_callback_failure_cannot_leak_deployment(lane, client, target):
    record = Mock(side_effect=OSError("cannot record evidence"))
    client.serving.get_deployment.side_effect = [
        SimpleNamespace(status="STOPPED", instances=[])
    ]
    with pytest.raises(OSError, match="cannot record evidence"):
        lane.qualify(client, target, record)
    client.serving.stop_deployment.assert_called_once()
