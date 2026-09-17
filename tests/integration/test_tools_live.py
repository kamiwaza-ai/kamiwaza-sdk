from __future__ import annotations

import pytest

from kamiwaza_sdk.schemas.tools import ToolDeployment, ToolDiscovery, ToolTemplate

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def test_legacy_tool_shed_catalog_and_discovery(live_kamiwaza_client) -> None:
    """Exercise the read-only 1.2.1 ToolService surface, not MCP execution."""
    service = live_kamiwaza_client.tools

    garden = service.get_garden_status()
    assert isinstance(garden["tool_servers_available"], bool)
    assert isinstance(garden["missing_tool_servers"], list)

    available = service.list_available_templates()
    imported = service.list_imported_templates()
    assert all(isinstance(item, ToolTemplate) for item in available)
    assert all(isinstance(item, ToolTemplate) for item in imported)

    deployments = service.list_deployments()
    assert all(isinstance(item, ToolDeployment) for item in deployments)

    discovery = service.discover_servers()
    assert isinstance(discovery, ToolDiscovery)
