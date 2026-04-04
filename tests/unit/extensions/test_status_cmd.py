"""Tests for the kz-ext status command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from kamiwaza_extensions.cli import app

runner = CliRunner()

pytestmark = pytest.mark.unit


def _mock_status():
    """Return a mock ExtensionStatus object."""
    from kamiwaza_sdk.schemas.extensions import (
        ExtensionEvent,
        ExtensionStatus,
        PodInfo,
        ServiceStatusDetail,
    )

    return ExtensionStatus(
        name="myapp-dev-abc123",
        phase="Running",
        url="https://cluster.test/runtime/apps/myapp",
        services=[
            ServiceStatusDetail(
                name="backend",
                image_tag="v1.0.0-g1234567",
                ready_replicas=1,
                replicas=1,
                pods=[PodInfo(name="backend-pod-1", phase="Running", ready=True)],
            ),
            ServiceStatusDetail(
                name="frontend",
                image_tag="v1.0.0-g1234567",
                ready_replicas=1,
                replicas=1,
                pods=[PodInfo(name="frontend-pod-1", phase="Running", ready=True)],
            ),
        ],
        events=[
            ExtensionEvent(type="Normal", reason="Scheduled", message="Pod scheduled"),
        ],
    )


@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_status_with_name(mock_conn_cls, mock_client_cls):
    """Status command with explicit --name should display table."""
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension_status.return_value = _mock_status()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["status", "--name", "myapp-dev-abc123"])

    assert result.exit_code == 0
    assert "myapp-dev-abc123" in result.output
    assert "Running" in result.output
    assert "backend" in result.output
    assert "frontend" in result.output


@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_status_not_found(mock_conn_cls, mock_client_cls):
    """Status for nonexistent extension should show error."""
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

    result = runner.invoke(app, ["status", "--name", "missing"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()
