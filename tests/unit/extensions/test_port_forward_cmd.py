"""Tests for the kz-ext port-forward command."""

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


# ------------------------------------------------------------------
# Happy path: auto-detect service, run kubectl port-forward
# ------------------------------------------------------------------


@patch("os.execvp")
@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_port_forward_happy_path(mock_conn_cls, mock_client_cls, mock_execvp):
    """Port-forward should exec kubectl port-forward for first running pod."""
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.return_value = _make_status_with_pods()
    mock_client_cls.return_value = mock_client

    from kamiwaza_extensions.commands.port_forward import run_port_forward

    run_port_forward(name="myapp-dev-abc123")

    mock_execvp.assert_called_once()
    cmd = mock_execvp.call_args[0][1]
    assert cmd[0] == "kubectl"
    assert "port-forward" in cmd
    assert "-n" in cmd
    assert "kamiwaza-extensions" in cmd
    assert "pod/backend-pod-1" in cmd
    # Default port should be 8000 (first common port)
    assert "8000:8000" in cmd


# ------------------------------------------------------------------
# Explicit --service and --port flags
# ------------------------------------------------------------------


@patch("os.execvp")
@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_port_forward_explicit_service(mock_conn_cls, mock_client_cls, mock_execvp):
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

    from kamiwaza_extensions.commands.port_forward import run_port_forward

    run_port_forward(name="myapp-dev-abc123", service="frontend")

    cmd = mock_execvp.call_args[0][1]
    assert "pod/frontend-pod-1" in cmd


@patch("os.execvp")
@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_port_forward_explicit_port(mock_conn_cls, mock_client_cls, mock_execvp):
    """--port should use the specified port number."""
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.return_value = _make_status_with_pods()
    mock_client_cls.return_value = mock_client

    from kamiwaza_extensions.commands.port_forward import run_port_forward

    run_port_forward(name="myapp-dev-abc123", port=9090)

    cmd = mock_execvp.call_args[0][1]
    assert "9090:9090" in cmd


# ------------------------------------------------------------------
# Extension not found (404)
# ------------------------------------------------------------------


@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_port_forward_not_found(mock_conn_cls, mock_client_cls):
    """Should exit with error if extension not found."""
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

    from kamiwaza_extensions.commands.port_forward import run_port_forward

    with pytest.raises((SystemExit, ClickExit)):
        run_port_forward(name="missing-ext")


@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_port_forward_api_error_404(mock_conn_cls, mock_client_cls):
    """Should exit with error on APIError with status_code=404."""
    from kamiwaza_sdk.exceptions import APIError

    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.side_effect = APIError(
        "Not found", status_code=404
    )
    mock_client_cls.return_value = mock_client

    from kamiwaza_extensions.commands.port_forward import run_port_forward

    with pytest.raises((SystemExit, ClickExit)):
        run_port_forward(name="missing-ext")


# ------------------------------------------------------------------
# No running pods
# ------------------------------------------------------------------


@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_port_forward_no_running_pods(mock_conn_cls, mock_client_cls):
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
                name="backend",
                image_tag="v1",
                ready_replicas=0,
                replicas=1,
                pods=[],
            ),
        ],
    )
    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.return_value = status
    mock_client_cls.return_value = mock_client

    from kamiwaza_extensions.commands.port_forward import run_port_forward

    with pytest.raises((SystemExit, ClickExit)):
        run_port_forward(name="myapp-dev-abc123")


# ------------------------------------------------------------------
# No connection configured
# ------------------------------------------------------------------


@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_port_forward_no_connection(mock_conn_cls):
    """Should exit with error if no active connection."""
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = None
    mock_conn_cls.return_value = mock_conn

    from kamiwaza_extensions.commands.port_forward import run_port_forward

    with pytest.raises((SystemExit, ClickExit)):
        run_port_forward(name="myapp-dev-abc123")


@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_port_forward_no_token(mock_conn_cls):
    """Should exit with error if token is expired/missing."""
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = None
    mock_conn_cls.return_value = mock_conn

    from kamiwaza_extensions.commands.port_forward import run_port_forward

    with pytest.raises((SystemExit, ClickExit)):
        run_port_forward(name="myapp-dev-abc123")
