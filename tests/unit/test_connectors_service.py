from __future__ import annotations

import uuid

import pytest

from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.connectors import M365_DEFAULT_SCOPES
from kamiwaza_sdk.services.connectors import ConnectorService

pytestmark = pytest.mark.unit

_TS = "2026-01-01T00:00:00Z"


class DummyClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return self.responses[("POST", path)]

    def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        return self.responses[("GET", path)]


def _connector(name="Microsoft 365"):
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "connector_type": "m365",
        "enabled": True,
        "scopes": M365_DEFAULT_SCOPES,
        "created_at": _TS,
    }


def test_create_m365_builds_config_and_default_scopes():
    resp = _connector()
    client = DummyClient({("POST", "/connectors"): resp})
    service = ConnectorService(client)

    conn = service.create_m365(tenant_id="tenant-abc", client_id="client-xyz")

    assert conn.connector_type == "m365"
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "/connectors")
    body = kwargs["json"]
    assert body["connector_type"] == "m365"
    assert body["config"] == {"tenant_id": "tenant-abc", "client_id": "client-xyz"}
    # tenant/client are the only config — no client_secret.
    assert "client_secret" not in body["config"]
    assert body["scopes"] == M365_DEFAULT_SCOPES
    assert body["name"] == "Microsoft 365"


def test_create_m365_custom_name_and_scopes():
    resp = _connector(name="Corp M365")
    client = DummyClient({("POST", "/connectors"): resp})
    service = ConnectorService(client)

    service.create_m365(
        tenant_id="t", client_id="c", name="Corp M365", scopes=["User.Read"]
    )

    body = client.calls[0][2]["json"]
    assert body["name"] == "Corp M365"
    assert body["scopes"] == ["User.Read"]


def test_list_unwraps_items_envelope():
    client = DummyClient({("GET", "/connectors"): {"items": [_connector()]}})
    service = ConnectorService(client)

    out = service.list()

    assert len(out) == 1
    assert out[0].connector_type == "m365"


class _RaisingClient:
    """Raises a 404 APIError on get/delete to exercise NotFoundError mapping."""

    def get(self, path, **kwargs):
        raise APIError("not found", status_code=404)

    def delete(self, path, **kwargs):
        raise APIError("not found", status_code=404)


def test_get_maps_404_to_not_found():
    service = ConnectorService(_RaisingClient())
    with pytest.raises(NotFoundError):
        service.get(uuid.uuid4())


def test_delete_maps_404_to_not_found():
    service = ConnectorService(_RaisingClient())
    with pytest.raises(NotFoundError):
        service.delete(uuid.uuid4())
