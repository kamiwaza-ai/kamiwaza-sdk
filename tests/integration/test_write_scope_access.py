"""Authorization regression tests at write scope.

These tests use ``live_write_client`` (write-scoped PAT: roles user, editor,
viewer — no admin) to verify that non-admin endpoints remain accessible without
elevated privileges.  If a future change gates one of these endpoints on the
admin role, these tests will surface the regression as a 403.

See ENG-2861 for context on the admin vs write scope split.
"""

from __future__ import annotations

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError  # noqa: F401 – used in pytest.raises


# ---------------------------------------------------------------------------
# Health / status endpoints — should always work for authenticated users
# ---------------------------------------------------------------------------


def test_auth_health_at_write_scope(live_write_client: KamiwazaClient) -> None:
    """GET /auth/health should not require admin."""
    health = live_write_client.get("/auth/health")
    assert health.get("status") == "healthy"


def test_serving_status_at_write_scope(live_write_client: KamiwazaClient) -> None:
    """GET /serving/status should not require admin."""
    status = live_write_client.serving.get_status()
    assert status is not None


def test_catalog_health_at_write_scope(live_write_client: KamiwazaClient) -> None:
    """GET /catalog health/metadata should not require admin."""
    datasets = live_write_client.catalog.list_datasets()
    assert isinstance(datasets, list)


# ---------------------------------------------------------------------------
# Extension listing — should work for regular users
# ---------------------------------------------------------------------------


def test_list_extensions_at_write_scope(live_write_client: KamiwazaClient) -> None:
    """GET /extensions should not require admin."""
    extensions = live_write_client.extensions.list_extensions()
    assert isinstance(extensions, list)


def test_app_garden_status_at_write_scope(live_write_client: KamiwazaClient) -> None:
    """GET /apps/garden/status should not require admin."""
    status = live_write_client.get("/apps/garden/status")
    assert "garden_apps_available" in status


# ---------------------------------------------------------------------------
# Model listing — should work for users with appropriate ReBAC relations
# ---------------------------------------------------------------------------


def test_list_models_at_write_scope(live_write_client: KamiwazaClient) -> None:
    """GET /models should not require admin."""
    models = live_write_client.models.list_models()
    assert isinstance(models, list)


# ---------------------------------------------------------------------------
# Current user — fundamental authenticated operation
# ---------------------------------------------------------------------------


def test_current_user_at_write_scope(live_write_client: KamiwazaClient) -> None:
    """Current user resolution should not require admin."""
    user = live_write_client.auth.get_current_user()
    assert user is not None
    assert user.username is not None


# ---------------------------------------------------------------------------
# Admin-gated endpoints — verify they DO require admin (negative tests)
# ---------------------------------------------------------------------------


def test_cluster_requires_admin_scope(live_write_client: KamiwazaClient) -> None:
    """Cluster endpoints should reject write-scoped tokens."""
    with pytest.raises(APIError) as exc_info:
        live_write_client.cluster.list_clusters()
    assert exc_info.value.status_code == 403
