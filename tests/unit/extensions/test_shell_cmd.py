"""Tests for the kz-ext shell command."""

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


@patch("os.execvp")
@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_shell_targets_first_running_pod(mock_conn_cls, mock_client_cls, mock_execvp):
    """Shell should exec into the first running pod."""
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.return_value = _make_status_with_pods()
    mock_client_cls.return_value = mock_client

    from kamiwaza_extensions.commands.shell import run_shell

    run_shell(name="myapp-dev-abc123")

    mock_execvp.assert_called_once()
    cmd = mock_execvp.call_args[0][1]
    assert cmd[0] == "kubectl"
    assert "exec" in cmd
    assert "-it" in cmd
    assert "backend-pod-1" in cmd
    assert "-n" in cmd
    assert "kamiwaza-extensions" in cmd
    assert "/bin/sh" in cmd


@patch("os.execvp")
@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_shell_targets_specific_service(mock_conn_cls, mock_client_cls, mock_execvp):
    """--service should target that service's pod."""
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.return_value = _make_status_with_pods()
    mock_client_cls.return_value = mock_client

    from kamiwaza_extensions.commands.shell import run_shell

    run_shell(name="myapp-dev-abc123", service="frontend")

    cmd = mock_execvp.call_args[0][1]
    assert "frontend-pod-1" in cmd


@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_shell_no_running_pods_exits(mock_conn_cls, mock_client_cls):
    """Should exit with error if no running pods."""
    from kamiwaza_sdk.schemas.extensions import ExtensionStatus, ServiceStatusDetail

    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    status = ExtensionStatus(
        name="myapp-dev-abc123",
        phase="Pending",
        services=[
            ServiceStatusDetail(
                name="backend", image_tag="v1", ready_replicas=0, replicas=1, pods=[]
            ),
        ],
    )
    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.return_value = status
    mock_client_cls.return_value = mock_client

    from kamiwaza_extensions.commands.shell import run_shell

    with pytest.raises((SystemExit, ClickExit)):
        run_shell(name="myapp-dev-abc123")


@patch("os.execvp")
@patch("kamiwaza_extensions.commands.shell.subprocess.run")
@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_shell_falls_back_to_kubectl_lookup(
    mock_conn_cls,
    mock_client_cls,
    mock_run,
    mock_execvp,
):
    """Fallback to kubectl pod lookup when the status endpoint is unavailable."""
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

    mock_run.return_value = MagicMock(returncode=0, stdout="frontend-pod-1\tRunning\n")

    from kamiwaza_extensions.commands.shell import run_shell

    run_shell(name="myapp-dev-abc123", service="frontend")

    lookup_cmd = mock_run.call_args[0][0]
    assert lookup_cmd[:3] == ["kubectl", "get", "pods"]
    assert (
        "extensions.kamiwaza.io/deployment-id=myapp-dev-abc123,extensions.kamiwaza.io/service=frontend"
        in lookup_cmd
    )

    exec_cmd = mock_execvp.call_args[0][1]
    assert "frontend-pod-1" in exec_cmd


@patch("os.execvp")
@patch("kamiwaza_extensions.commands.shell.subprocess.run")
@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_shell_not_found_exits_without_kubectl_lookup(
    mock_conn_cls,
    mock_client_cls,
    mock_run,
    mock_execvp,
):
    """Missing extensions should exit before trying kubectl lookup."""
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

    from kamiwaza_extensions.commands.shell import run_shell

    with pytest.raises((SystemExit, ClickExit)):
        run_shell(name="myapp-dev-abc123")

    mock_run.assert_not_called()
    mock_execvp.assert_not_called()
