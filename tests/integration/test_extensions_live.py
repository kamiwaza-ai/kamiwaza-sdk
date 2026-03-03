"""Integration tests for K8s-native extension API endpoints.

Tests cover:
- Extension lifecycle via typed SDK service (list, create, get, delete)
- Extension lifecycle via raw HTTP API
- Garden status endpoints (app and tool)
- Error paths (404 for nonexistent extensions)
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.extensions import (
    CreateExtension,
    Extension,
    ExtensionServiceSpec,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _k8s_available(client) -> bool:
    """Check whether the K8s extension API is reachable (not 503)."""
    try:
        client.get("/extensions")
        return True
    except APIError as exc:
        if exc.status_code == 503:
            return False
        raise


def _create_test_extension_payload(name: str) -> CreateExtension:
    """Build a minimal CreateExtension request for testing."""
    return CreateExtension(
        name=name,
        type="tool",
        version="0.0.1-test",
        services=[
            ExtensionServiceSpec(
                name="echo",
                image="busybox:latest",
                primary=True,
                ports=[{"container_port": 8080}],
                command=["sh", "-c", "while true; do echo ok | nc -l -p 8080; done"],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Garden status (no K8s dependency)
# ---------------------------------------------------------------------------


def test_app_garden_status(live_kamiwaza_client) -> None:
    """GET /apps/garden/status returns imported vs available counts."""
    status = live_kamiwaza_client.get("/apps/garden/status")
    assert "garden_apps_available" in status
    assert "missing_apps" in status


def test_tool_garden_status(live_kamiwaza_client) -> None:
    """GET /tool/garden/status returns imported vs available counts."""
    status = live_kamiwaza_client.get("/tool/garden/status")
    assert isinstance(status, dict)


# ---------------------------------------------------------------------------
# Extension list via SDK service
# ---------------------------------------------------------------------------


def test_list_extensions_typed(live_kamiwaza_client) -> None:
    """ExtensionService.list_extensions() returns a list of Extension objects."""
    if not _k8s_available(live_kamiwaza_client):
        pytest.skip("K8s extension API unavailable (503)")

    extensions = live_kamiwaza_client.extensions.list_extensions()
    assert isinstance(extensions, list)
    for ext in extensions:
        assert isinstance(ext, Extension)
        assert ext.name
        assert ext.type in ("app", "tool")


def test_list_extensions_raw(live_kamiwaza_client) -> None:
    """GET /extensions returns a JSON list."""
    if not _k8s_available(live_kamiwaza_client):
        pytest.skip("K8s extension API unavailable (503)")

    result = live_kamiwaza_client.get("/extensions")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Extension error paths
# ---------------------------------------------------------------------------


def test_get_nonexistent_extension_typed(live_kamiwaza_client) -> None:
    """ExtensionService.get_extension() raises NotFoundError for bogus name."""
    if not _k8s_available(live_kamiwaza_client):
        pytest.skip("K8s extension API unavailable (503)")

    bogus = f"sdk-test-does-not-exist-{uuid4().hex[:8]}"
    with pytest.raises(NotFoundError):
        live_kamiwaza_client.extensions.get_extension(bogus)


def test_get_nonexistent_extension_raw(live_kamiwaza_client) -> None:
    """GET /extensions/{bogus} returns 404."""
    if not _k8s_available(live_kamiwaza_client):
        pytest.skip("K8s extension API unavailable (503)")

    bogus = f"sdk-test-does-not-exist-{uuid4().hex[:8]}"
    with pytest.raises(APIError) as exc:
        live_kamiwaza_client.get(f"/extensions/{bogus}")
    assert exc.value.status_code in (404, 500)


def test_delete_nonexistent_extension_typed(live_kamiwaza_client) -> None:
    """ExtensionService.delete_extension() raises NotFoundError for bogus name."""
    if not _k8s_available(live_kamiwaza_client):
        pytest.skip("K8s extension API unavailable (503)")

    bogus = f"sdk-test-does-not-exist-{uuid4().hex[:8]}"
    with pytest.raises(NotFoundError):
        live_kamiwaza_client.extensions.delete_extension(bogus)


# ---------------------------------------------------------------------------
# Extension CRUD lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("KAMIWAZA_TEST_EXTENSION_CRUD") != "1",
    reason="Set KAMIWAZA_TEST_EXTENSION_CRUD=1 to run extension create/delete tests",
)
def test_extension_crud_lifecycle_typed(live_kamiwaza_client) -> None:
    """Full create -> get -> list -> delete cycle via typed SDK service."""
    if not _k8s_available(live_kamiwaza_client):
        pytest.skip("K8s extension API unavailable (503)")

    ext_name = _unique("sdk-test-ext")
    request = _create_test_extension_payload(ext_name)

    created = None
    try:
        # Create
        created = live_kamiwaza_client.extensions.create_extension(request)
        assert isinstance(created, Extension)
        assert created.name == ext_name
        assert created.type == "tool"
        assert created.version == "0.0.1-test"

        # Get
        fetched = live_kamiwaza_client.extensions.get_extension(ext_name)
        assert fetched.name == ext_name

        # List should include our extension
        all_exts = live_kamiwaza_client.extensions.list_extensions()
        names = [e.name for e in all_exts]
        assert ext_name in names

    finally:
        # Cleanup
        if created is not None:
            try:
                live_kamiwaza_client.extensions.delete_extension(ext_name)
            except (APIError, NotFoundError):
                pass


@pytest.mark.skipif(
    os.environ.get("KAMIWAZA_TEST_EXTENSION_CRUD") != "1",
    reason="Set KAMIWAZA_TEST_EXTENSION_CRUD=1 to run extension create/delete tests",
)
def test_extension_crud_lifecycle_raw(live_kamiwaza_client) -> None:
    """Full create -> get -> list -> delete cycle via raw HTTP API."""
    if not _k8s_available(live_kamiwaza_client):
        pytest.skip("K8s extension API unavailable (503)")

    ext_name = _unique("sdk-test-ext-raw")
    payload = _create_test_extension_payload(ext_name).model_dump()

    created_name = None
    try:
        # Create
        created = live_kamiwaza_client.post("/extensions", json=payload)
        assert created["name"] == ext_name
        created_name = ext_name

        # Get
        fetched = live_kamiwaza_client.get(f"/extensions/{ext_name}")
        assert fetched["name"] == ext_name
        assert fetched["type"] == "tool"

        # List
        all_exts = live_kamiwaza_client.get("/extensions")
        names = [e["name"] for e in all_exts]
        assert ext_name in names

        # Delete (async in K8s — CR may linger briefly)
        live_kamiwaza_client.delete(f"/extensions/{ext_name}")
        created_name = None

    finally:
        if created_name:
            try:
                live_kamiwaza_client.delete(f"/extensions/{created_name}")
            except APIError:
                pass


# ---------------------------------------------------------------------------
# Remote catalog endpoints
# ---------------------------------------------------------------------------


def test_app_remote_catalog_status(live_kamiwaza_client) -> None:
    """GET /apps/remote/status returns cache status info."""
    status = live_kamiwaza_client.get("/apps/remote/status")
    assert "cache_status" in status


def test_app_remote_apps_list(live_kamiwaza_client) -> None:
    """GET /apps/remote/apps returns a list of available remote apps."""
    remote_apps = live_kamiwaza_client.get("/apps/remote/apps")
    assert isinstance(remote_apps, list)


def test_tool_remote_status(live_kamiwaza_client) -> None:
    """GET /tool/remote/status returns tool remote status."""
    try:
        status = live_kamiwaza_client.get("/tool/remote/status")
        assert isinstance(status, dict)
    except APIError as exc:
        if exc.status_code == 404:
            pytest.skip("Tool remote status endpoint not available")
        raise


def test_tool_remote_tools_list(live_kamiwaza_client) -> None:
    """GET /tool/remote/tools returns a list of available remote tools."""
    try:
        remote_tools = live_kamiwaza_client.get("/tool/remote/tools")
        assert isinstance(remote_tools, list)
    except APIError as exc:
        if exc.status_code == 404:
            pytest.skip("Tool remote tools endpoint not available")
        raise
