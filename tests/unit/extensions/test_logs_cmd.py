"""Tests for the kz-ext logs command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.exceptions import Exit as ClickExit

pytestmark = pytest.mark.unit


def _make_status_with_pods():
    from kamiwaza_sdk.schemas.extensions import (
        ExtensionStatus,
        PodInfo,
        ServiceStatusDetail,
    )

    return ExtensionStatus(
        name="myapp-dev-abc123",
        phase="Running",
        services=[
            ServiceStatusDetail(
                name="backend",
                image_tag="v1",
                ready_replicas=1,
                replicas=1,
                pods=[
                    PodInfo(name="backend-pod-1", phase="Running", ready=True),
                ],
            ),
            ServiceStatusDetail(
                name="frontend",
                image_tag="v1",
                ready_replicas=1,
                replicas=1,
                pods=[
                    PodInfo(name="frontend-pod-1", phase="Running", ready=True),
                ],
            ),
        ],
    )


@patch("kamiwaza_extensions.commands.logs.subprocess.run")
@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_logs_uses_pod_name_from_status(mock_conn_cls, mock_client_cls, mock_run):
    """When status endpoint returns pods, kubectl should target the pod by name."""
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.return_value = _make_status_with_pods()
    mock_client_cls.return_value = mock_client

    mock_run.return_value = MagicMock(returncode=0)

    from kamiwaza_extensions.commands.logs import run_logs

    with pytest.raises((SystemExit, ClickExit)):
        run_logs(name="myapp-dev-abc123")

    cmd = mock_run.call_args[0][0]
    assert "kubectl" in cmd
    assert "logs" in cmd
    assert "backend-pod-1" in cmd
    assert "-n" in cmd
    assert "kamiwaza-extensions" in cmd


@patch("kamiwaza_extensions.commands.logs.subprocess.run")
@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_logs_follow_and_tail_flags(mock_conn_cls, mock_client_cls, mock_run):
    """--follow and --tail should be passed to kubectl."""
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.return_value = _make_status_with_pods()
    mock_client_cls.return_value = mock_client

    mock_run.return_value = MagicMock(returncode=0)

    from kamiwaza_extensions.commands.logs import run_logs

    with pytest.raises((SystemExit, ClickExit)):
        run_logs(name="myapp-dev-abc123", follow=True, tail=50)

    cmd = mock_run.call_args[0][0]
    assert "--follow" in cmd
    assert "--tail" in cmd
    assert "50" in cmd


@patch("kamiwaza_extensions.commands.logs.subprocess.run")
@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_logs_service_filter(mock_conn_cls, mock_client_cls, mock_run):
    """--service should target only that service's pods."""
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.return_value = _make_status_with_pods()
    mock_client_cls.return_value = mock_client

    mock_run.return_value = MagicMock(returncode=0)

    from kamiwaza_extensions.commands.logs import run_logs

    with pytest.raises((SystemExit, ClickExit)):
        run_logs(name="myapp-dev-abc123", service="frontend")

    cmd = mock_run.call_args[0][0]
    assert "frontend-pod-1" in cmd


@patch("kamiwaza_extensions.commands.logs.subprocess.run")
@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_logs_falls_back_to_label_selector(mock_conn_cls, mock_client_cls, mock_run):
    """When status endpoint fails, should use label selector."""
    from kamiwaza_sdk.exceptions import APIError

    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.side_effect = APIError(
        "Not supported", status_code=405
    )
    mock_client_cls.return_value = mock_client

    mock_run.return_value = MagicMock(returncode=0)

    from kamiwaza_extensions.commands.logs import run_logs

    with pytest.raises((SystemExit, ClickExit)):
        run_logs(name="myapp-dev-abc123")

    cmd = mock_run.call_args[0][0]
    assert "-l" in cmd
    assert "extensions.kamiwaza.io/deployment-id=myapp-dev-abc123" in cmd


@patch("kamiwaza_extensions.commands.logs.subprocess.run")
@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_logs_not_found_exits_without_kubectl(mock_conn_cls, mock_client_cls, mock_run):
    """Missing extensions should not fall through to kubectl logs."""
    from kamiwaza_sdk.exceptions import NotFoundError

    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.side_effect = NotFoundError("missing")
    mock_client_cls.return_value = mock_client

    from kamiwaza_extensions.commands.logs import run_logs

    with pytest.raises((SystemExit, ClickExit)):
        run_logs(name="myapp-dev-abc123")

    mock_run.assert_not_called()
