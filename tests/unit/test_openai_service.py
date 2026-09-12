from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.services.openai import OpenAIService


def _client(active, current, *, token="token"):
    client = Mock()
    client.serving.list_active_deployments.side_effect = active
    client.serving.get_deployment.return_value = current
    client.get_bearer_token.return_value = token
    client.session.verify = True
    return client


@pytest.mark.parametrize("status", ["ERROR", "FAILED"])
def test_get_client_waits_for_native_replacement_to_become_active(monkeypatch, status):
    deployment_id = uuid4()
    active = [[], [], [SimpleNamespace(id=deployment_id, endpoint="https://runtime/v1")]]
    current = SimpleNamespace(
        status=status, last_error_code="NATIVE_CPU_HEALTH_UNVERIFIED"
    )
    client = _client(active, current)
    sentinel = object()
    monkeypatch.setattr("kamiwaza_sdk.services.openai.OpenAI", Mock(return_value=sentinel))
    monkeypatch.setattr("kamiwaza_sdk.services.openai.time.sleep", lambda _: None)

    result = OpenAIService(client).get_client(deployment_id=deployment_id)

    assert result is sentinel
    assert client.serving.list_active_deployments.call_count == 3
    assert client.serving.get_deployment.call_count == 1


def test_get_client_does_not_retry_unrelated_error(monkeypatch):
    deployment_id = uuid4()
    current = SimpleNamespace(status="ERROR", last_error_code="MODEL_LOAD_FAILED")
    client = _client([[]], current)
    monkeypatch.setattr("kamiwaza_sdk.services.openai.time.sleep", Mock())

    with pytest.raises(ValueError, match="No active deployment"):
        OpenAIService(client).get_client(deployment_id=deployment_id)

    client.serving.list_active_deployments.assert_called_once()
    client.serving.get_deployment.assert_called_once_with(deployment_id)


def test_get_client_preserves_missing_deployment_error(monkeypatch):
    deployment_id = uuid4()
    client = _client([[]], None)
    client.serving.get_deployment.side_effect = APIError("gone")
    monkeypatch.setattr("kamiwaza_sdk.services.openai.time.sleep", Mock())

    with pytest.raises(ValueError, match="No active deployment"):
        OpenAIService(client).get_client(deployment_id=deployment_id)
