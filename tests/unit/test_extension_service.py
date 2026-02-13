"""Unit tests for the ExtensionService SDK client."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.extensions import (
    CreateExtension,
    Extension,
    ExtensionServiceSpec,
)
from kamiwaza_sdk.services.extensions import ExtensionService

pytestmark = pytest.mark.unit


class DummyClient:
    """Minimal test double that records calls and returns canned responses."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def _dispatch(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        key = (method, path)
        if key not in self.responses:
            raise RuntimeError(f"No canned response for {key}")
        resp = self.responses[key]
        if isinstance(resp, Exception):
            raise resp
        return resp

    def get(self, path: str, **kwargs):
        return self._dispatch("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self._dispatch("POST", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self._dispatch("DELETE", path, **kwargs)


# -- list --


def test_list_extensions():
    responses = {
        ("GET", "/extensions"): [
            {"name": "ext-a", "type": "app", "version": "1.0"},
            {"name": "ext-b", "type": "tool", "version": "2.0"},
        ]
    }
    service = ExtensionService(DummyClient(responses))

    result = service.list_extensions()

    assert len(result) == 2
    assert all(isinstance(e, Extension) for e in result)
    assert result[0].name == "ext-a"
    assert result[1].type == "tool"


def test_list_extensions_empty():
    service = ExtensionService(DummyClient({("GET", "/extensions"): []}))
    assert service.list_extensions() == []


# -- get --


def test_get_extension():
    responses = {
        ("GET", "/extensions/kaizen"): {
            "name": "kaizen",
            "type": "app",
            "version": "1.3.4",
            "phase": "Running",
            "services": [
                {
                    "name": "backend",
                    "ready": True,
                    "replicas": 1,
                    "available_replicas": 1,
                },
            ],
            "endpoints": {
                "external": "https://kamiwaza.test/runtime/apps/kaizen",
            },
        }
    }
    service = ExtensionService(DummyClient(responses))

    ext = service.get_extension("kaizen")

    assert ext.name == "kaizen"
    assert ext.phase == "Running"
    assert len(ext.services) == 1
    assert ext.services[0].ready is True
    assert ext.endpoints is not None
    assert "kaizen" in ext.endpoints.external


def test_get_extension_not_found():
    error = APIError("Not found", status_code=404)
    responses = {("GET", "/extensions/missing"): error}
    service = ExtensionService(DummyClient(responses))

    with pytest.raises(NotFoundError, match="missing"):
        service.get_extension("missing")


def test_get_extension_reraises_non_404():
    error = APIError("Server error", status_code=500)
    responses = {("GET", "/extensions/broken"): error}
    service = ExtensionService(DummyClient(responses))

    with pytest.raises(APIError, match="Server error"):
        service.get_extension("broken")


# -- create --


def test_create_extension():
    responses = {
        ("POST", "/extensions"): {
            "name": "new-ext",
            "type": "app",
            "version": "1.0.0",
            "phase": "Pending",
        }
    }
    service = ExtensionService(DummyClient(responses))

    req = CreateExtension(
        name="new-ext",
        type="app",
        version="1.0.0",
        services=[
            ExtensionServiceSpec(name="backend", image="img:latest", primary=True),
        ],
    )
    ext = service.create_extension(req)

    assert ext.name == "new-ext"
    assert ext.phase == "Pending"
    method, path, kwargs = service.client.calls[0]
    assert method == "POST"
    assert path == "/extensions"
    assert "json" in kwargs


# -- delete --


def test_delete_extension():
    responses = {("DELETE", "/extensions/old-ext"): None}
    client = DummyClient(responses)
    service = ExtensionService(client)

    result = service.delete_extension("old-ext")

    assert result is True
    assert client.calls[0] == ("DELETE", "/extensions/old-ext", {})


def test_delete_extension_not_found():
    error = APIError("Not found", status_code=404)
    responses = {("DELETE", "/extensions/missing"): error}
    service = ExtensionService(DummyClient(responses))

    with pytest.raises(NotFoundError, match="missing"):
        service.delete_extension("missing")


def test_delete_extension_reraises_non_404():
    error = APIError("Forbidden", status_code=403)
    responses = {("DELETE", "/extensions/ext"): error}
    service = ExtensionService(DummyClient(responses))

    with pytest.raises(APIError, match="Forbidden"):
        service.delete_extension("ext")


# -- schema validation --


def test_create_extension_rejects_invalid_type():
    with pytest.raises(ValidationError, match="type"):
        CreateExtension(
            name="test",
            type="invalid",
            version="1.0.0",
            services=[
                ExtensionServiceSpec(name="svc", image="img:latest", primary=True),
            ],
        )
