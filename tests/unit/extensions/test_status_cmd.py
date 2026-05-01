"""Tests for the kz-ext status command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from kamiwaza_extensions.cli import app

runner = CliRunner()

pytestmark = pytest.mark.unit


def _mock_extension(annotations: dict | None = None):
    """Return a mock Extension response (the P10 endpoint).

    `Extension` has `extra="allow"`, so annotations ride on `model_extra`.
    """
    from kamiwaza_sdk.schemas.extensions import (
        Extension,
        ExtensionEndpoints,
        ExtensionServiceStatus,
    )

    payload = {
        "name": "myapp-dev-abc123",
        "type": "app",
        "version": "1.0.0",
        "phase": "Running",
        "endpoints": ExtensionEndpoints(
            external="https://cluster.test/runtime/apps/myapp"
        ),
        "services": [
            ExtensionServiceStatus(
                name="backend", ready=True, replicas=1, available_replicas=1
            ),
            ExtensionServiceStatus(
                name="frontend", ready=True, replicas=1, available_replicas=1
            ),
        ],
    }
    if annotations is not None:
        payload["annotations"] = annotations
    return Extension(**payload)


@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_status_with_name_hits_extensions_endpoint_not_status(
    mock_conn_cls, mock_client_cls
):
    """ENG-3887 P10: must call get_extension (/extensions/{name}), not the
    /status sibling that returns 404 on every cluster currently deployed."""
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension.return_value = _mock_extension()
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["status", "--name", "myapp-dev-abc123"])

    assert result.exit_code == 0
    mock_client.extensions.get_extension.assert_called_once_with("myapp-dev-abc123")
    # And we never hit the broken /status endpoint
    assert not mock_client.extensions.get_extension_status.called
    assert "myapp-dev-abc123" in result.output
    assert "Running" in result.output


@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
def test_status_surfaces_deployer_annotation(mock_conn_cls, mock_client_cls):
    """ENG-3887 §4.2.9: surface `kamiwaza.io/deployer` from CRD annotations.

    Namespace is ``kamiwaza.io/*`` per ENG-3901 / F-010 — the platform's
    annotation filter only persists ``kamiwaza.io/*`` keys."""
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api", verify_ssl=True
    )
    mock_conn.get_token.return_value = MagicMock(access_token="tok")
    mock_conn_cls.return_value = mock_conn

    mock_client = MagicMock()
    mock_client.extensions.get_extension.return_value = _mock_extension(
        annotations={
            "kamiwaza.io/deployer": "jonathan@kamiwaza.ai",
            "kamiwaza.io/revision": "1.0.0-dev-abc.123",
            "kamiwaza.io/deployed-at": "2026-04-28T20:00:00+00:00",
        },
    )
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["status", "--name", "myapp-dev-abc123"])

    assert result.exit_code == 0
    assert "Last deployed by" in result.output
    assert "jonathan@kamiwaza.ai" in result.output
    assert "1.0.0-dev-abc.123" in result.output


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
    mock_client.extensions.get_extension.side_effect = APIError(
        "Not found", status_code=404
    )
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["status", "--name", "missing"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# Review re-review PR #84 H2: cluster-mismatch fallback in name resolution
# ---------------------------------------------------------------------------


@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
@patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
@patch("kamiwaza_extensions.dev_state.read_state")
def test_status_falls_back_to_jwt_when_dev_state_cluster_mismatches(
    mock_read_state,
    mock_detector_cls,
    mock_conn_cls,
    mock_client_cls,
):
    """H2: after `kz-ext login` to a different cluster, `status` must NOT
    use the saved dev_name from the prior cluster — querying the new
    cluster for the old name returns a misleading 404. Fall back to the
    deterministic JWT-derived name so the new cluster is queried for the
    correct dev name."""
    from kamiwaza_extensions.dev_state import DevState

    # Active connection is now cluster B.
    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster-b.test/api",
        verify_ssl=True,
    )
    mock_conn.get_token.return_value = MagicMock(
        access_token="header.eyJzdWIiOiJ1c2VyLWIifQ.sig",
    )
    mock_conn_cls.return_value = mock_conn

    from types import SimpleNamespace

    mock_detector = MagicMock()
    mock_detector.detect.return_value = SimpleNamespace(
        path="/tmp/x",
        name="my-app",
        version="1.0.0",
    )
    mock_detector_cls.return_value = mock_detector

    # Dev-state from a prior deploy to cluster A.
    mock_read_state.return_value = DevState(
        last_dev_name="my-app-dev-old123",
        last_revision="rev-a",
        cluster="https://cluster-a.test/api",
        extension_name="my-app",
        deployer="alice@example.com",
        last_successful_step="poll",
    )

    mock_client = MagicMock()
    ext = MagicMock(
        name="my-app-dev-jwt",
        phase="Running",
        endpoints=MagicMock(external=None),
        services=[],
        model_extra={},
    )
    ext.annotations = {}
    mock_client.extensions.get_extension.return_value = ext
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    called_with = mock_client.extensions.get_extension.call_args[0][0]
    assert (
        called_with != "my-app-dev-old123"
    ), f"status used cluster-A dev_name {called_with!r} despite cluster-B connection"
    assert called_with.startswith("my-app-dev-")
    assert len(called_with.split("-")[-1]) == 6


@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
@patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
@patch("kamiwaza_extensions.dev_state.read_state")
def test_status_uses_dev_state_name_when_cluster_matches(
    mock_read_state,
    mock_detector_cls,
    mock_conn_cls,
    mock_client_cls,
):
    """Sanity check the H2 fix doesn't break the happy path."""
    from kamiwaza_extensions.dev_state import DevState

    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api",
        verify_ssl=True,
    )
    mock_conn.get_token.return_value = MagicMock(
        access_token="header.eyJzdWIiOiAidXNlci1hbGljZSIsICJlbWFpbCI6ICJhbGljZUBleGFtcGxlLmNvbSJ9.sig",
    )
    mock_conn_cls.return_value = mock_conn

    from types import SimpleNamespace

    mock_detector = MagicMock()
    mock_detector.detect.return_value = SimpleNamespace(
        path="/tmp/x",
        name="my-app",
        version="1.0.0",
    )
    mock_detector_cls.return_value = mock_detector

    mock_read_state.return_value = DevState(
        last_dev_name="my-app-dev-saved",
        last_revision="rev-1",
        cluster="https://cluster.test/api",
        extension_name="my-app",
        deployer="alice@example.com",
        last_successful_step="poll",
    )

    mock_client = MagicMock()
    ext = MagicMock(
        name="my-app-dev-saved",
        phase="Running",
        endpoints=MagicMock(external=None),
        services=[],
        model_extra={},
    )
    ext.annotations = {}
    mock_client.extensions.get_extension.return_value = ext
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    called_with = mock_client.extensions.get_extension.call_args[0][0]
    assert called_with == "my-app-dev-saved"


# ---------------------------------------------------------------------------
# Review re-re-review PR #84 H2: deployer guard on dev-state reuse
# ---------------------------------------------------------------------------


@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
@patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
@patch("kamiwaza_extensions.dev_state.read_state")
def test_status_falls_back_to_jwt_when_deployer_mismatches(
    mock_read_state,
    mock_detector_cls,
    mock_conn_cls,
    mock_client_cls,
):
    """Same cluster, but a *different user* is now logged in. Without
    the deployer guard, `kz-ext status` would query for user A's
    deployment and return its metadata to user B (or a 404 if A's
    deployment is gone). Dev names are per-user — fall back to the
    JWT-derived name so the new user sees their own deployment."""
    from kamiwaza_extensions.dev_state import DevState

    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api",
        verify_ssl=True,
    )
    # JWT carrying email=bob@example.com — different from state.deployer.
    mock_conn.get_token.return_value = MagicMock(
        access_token="header.eyJzdWIiOiAidXNlci1ib2IiLCAiZW1haWwiOiAiYm9iQGV4YW1wbGUuY29tIn0.sig",
    )
    mock_conn_cls.return_value = mock_conn

    from types import SimpleNamespace

    mock_detector = MagicMock()
    mock_detector.detect.return_value = SimpleNamespace(
        path="/tmp/x",
        name="my-app",
        version="1.0.0",
    )
    mock_detector_cls.return_value = mock_detector

    # Saved state from user A, same cluster as the active connection.
    mock_read_state.return_value = DevState(
        last_dev_name="my-app-dev-userA1",  # alice's deployment
        last_revision="rev-1",
        cluster="https://cluster.test/api",  # matches active connection
        extension_name="my-app",
        deployer="alice@example.com",  # ≠ bob's JWT
        last_successful_step="poll",
    )

    mock_client = MagicMock()
    ext = MagicMock(
        name="my-app-dev-userB",
        phase="Running",
        endpoints=MagicMock(external=None),
        services=[],
        model_extra={},
    )
    ext.annotations = {}
    mock_client.extensions.get_extension.return_value = ext
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    called_with = mock_client.extensions.get_extension.call_args[0][0]
    # Crucially NOT alice's saved name.
    assert (
        called_with != "my-app-dev-userA1"
    ), f"status surfaced alice's deployment {called_with!r} to bob"
    # Bob gets his own JWT-derived name.
    assert called_with.startswith("my-app-dev-")
    assert len(called_with.split("-")[-1]) == 6


# ---------------------------------------------------------------------------
# Review re-re-re-review PR #84 M4: case-insensitive email comparison
# ---------------------------------------------------------------------------


@patch("kamiwaza_sdk.KamiwazaClient")
@patch("kamiwaza_extensions.connections.ConnectionManager")
@patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
@patch("kamiwaza_extensions.dev_state.read_state")
def test_status_email_match_is_case_insensitive(
    mock_read_state,
    mock_detector_cls,
    mock_conn_cls,
    mock_client_cls,
):
    """Some IdPs vary email casing across token refreshes (`Alice@Example.com`
    one minute, `alice@example.com` the next). The deployer match should
    treat both as the same identity rather than silently fall back."""
    from kamiwaza_extensions.dev_state import DevState

    mock_conn = MagicMock()
    mock_conn.get_active_connection.return_value = MagicMock(
        url="https://cluster.test/api",
        verify_ssl=True,
    )
    # JWT carries `Alice@Example.COM` (mixed case).
    mock_conn.get_token.return_value = MagicMock(
        access_token="header.eyJzdWIiOiAidXNlci1hbGljZSIsICJlbWFpbCI6ICJBbGljZUBFeGFtcGxlLkNPTSJ9.sig",
    )
    mock_conn_cls.return_value = mock_conn

    from types import SimpleNamespace

    mock_detector = MagicMock()
    mock_detector.detect.return_value = SimpleNamespace(
        path="/tmp/x",
        name="my-app",
        version="1.0.0",
    )
    mock_detector_cls.return_value = mock_detector

    # State has the lowercase form of the same email.
    mock_read_state.return_value = DevState(
        last_dev_name="my-app-dev-saved",
        last_revision="1.0.0-dev-abc.1",
        cluster="https://cluster.test/api",
        extension_name="my-app",
        deployer="alice@example.com",  # ≠ JWT casing
        last_successful_step="poll",
    )

    mock_client = MagicMock()
    ext = MagicMock(
        name="my-app-dev-saved",
        phase="Running",
        endpoints=MagicMock(external=None),
        services=[],
        model_extra={},
    )
    ext.annotations = {}
    mock_client.extensions.get_extension.return_value = ext
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    # Saved name reused even though email casing differs.
    called_with = mock_client.extensions.get_extension.call_args[0][0]
    assert called_with == "my-app-dev-saved"
