"""Read-only M365 surface smoke; not a workroom-isolation evidence scenario.

The cross-workroom negative search and direct-fetch assertions in T13 require
distinct, approved fixture content. This test only proves that a connected
1.2.1 instance can browse and perform a correctly scoped search through the
public SDK without changing the existing connector or Microsoft 365 tenant.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from kamiwaza_sdk.schemas.connector_surfaces import (
    ConnectorBrowseRequest,
    ConnectorSearchRequest,
    ConnectorSurfaceRef,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

_M365_TYPES = frozenset({"m365", "microsoft365", "microsoft-365"})
_GLOBAL_WORKROOM_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"


def _enabled_m365_ids(client) -> set[UUID]:
    # Member-safe discovery: the admin-only full connector view is not needed.
    return {
        item.id
        for item in client.connectors.list_available()
        if item.enabled and item.connector_type.lower() in _M365_TYPES
    }


def _connected_files_ref(client, eligible: set[UUID]) -> ConnectorSurfaceRef | None:
    workrooms = (
        workroom
        for workroom in client.workrooms.list()
        if str(workroom.id) != _GLOBAL_WORKROOM_ID
    )
    for workroom in workrooms:
        entry = next(
            (
                item
                for item in client.connectors.list_surface_catalog(workroom.id)
                if _is_searchable_m365_files(item, eligible)
            ),
            None,
        )
        if entry is not None:
            return ConnectorSurfaceRef(
                workroom_id=str(workroom.id), connector_id=str(entry.id)
            )
    return None


def _is_searchable_m365_files(entry, eligible: set[UUID]) -> bool:
    return (
        entry.id in eligible
        and entry.connected
        and any(surface.surface == "files" for surface in entry.searchable_surfaces())
    )


def _folder_scope(client, target: ConnectorSurfaceRef) -> str | None:
    """Find a drive-root scope in at most three metadata pages."""
    page_token = None
    for _ in range(3):
        page = client.connectors.browse_surface(
            target,
            ConnectorBrowseRequest(
                surface="files", page_size=20, page_token=page_token
            ),
        )
        assert str(page.connector_id) == target.connector_id
        assert page.surface == "files"
        assert len(page.items) <= 20
        folder_id = next(
            (
                node.container_id
                for node in page.items
                if node.container_id and node.container_id.startswith("folder:")
            ),
            None,
        )
        if folder_id:
            return folder_id
        if not page.next_page_token:
            return None
        page_token = page.next_page_token
    return None


def test_connected_m365_files_browse_and_scoped_search(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client
    eligible = _enabled_m365_ids(client)
    if not eligible:
        pytest.skip("No enabled Microsoft 365 connector is available to this caller")

    target = _connected_files_ref(client, eligible)
    if target is None:
        pytest.skip("No connected, searchable M365 files surface in a workroom")

    # Search requires the folder-scoped container ID from a drive node, not
    # its node ID or a site-scoped container.
    folder_id = _folder_scope(client, target)
    if folder_id is None:
        pytest.skip("M365 connection has no visible drive root for a scoped search")

    query = f"sdk-no-match-{uuid4().hex}"
    result = client.connectors.search_surface(
        target,
        ConnectorSearchRequest(
            surface="files", container_id=folder_id, query=query, page_size=1
        ),
    )
    assert result.surface == "files"
    assert str(result.connector_id) == target.connector_id
    assert result.items == []
