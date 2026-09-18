from __future__ import annotations

import warnings

import pytest

from kamiwaza_sdk.services.tools import ToolService

pytestmark = pytest.mark.unit


class CatalogClient:
    def __init__(self, items: list[dict], path: str = "/tool/templates/available"):
        self.items = items
        self.path = path

    def get(self, path: str) -> list[dict]:
        assert path == self.path
        return self.items


def test_available_legacy_template_without_version_is_typed() -> None:
    client = CatalogClient([{"name": "tool-websearch", "description": "Search"}])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        service = ToolService(client)

    templates = service.list_available_templates()

    assert len(templates) == 1
    assert templates[0].name == "tool-websearch"
    assert templates[0].version is None


def test_available_version_is_preserved_when_present() -> None:
    client = CatalogClient(
        [{"name": "fixture", "description": "Test", "version": "1.2.1"}]
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        service = ToolService(client)

    assert service.list_available_templates()[0].version == "1.2.1"


def test_imported_legacy_template_accepts_null_capabilities() -> None:
    """ENG-12296: imported Tool Shed metadata may have no capabilities list."""
    client = CatalogClient(
        [{"name": "tool-websearch", "description": "Search", "capabilities": None}],
        path="/tool/templates",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        service = ToolService(client)

    assert service.list_imported_templates()[0].capabilities is None
