from __future__ import annotations

import uuid

import pytest

from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.connectors import ConnectorCreate, ConnectorUpdate
from kamiwaza_sdk.services.connectors import ConnectorService

pytestmark = pytest.mark.unit

_TS = "2026-01-01T00:00:00Z"
# Representative scopes for the example connector; the SDK no longer ships
# connector-specific scope defaults.
_SCOPES = ["Files.Read.All", "Mail.Read"]


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

    def put(self, path, **kwargs):
        self.calls.append(("PUT", path, kwargs))
        return self.responses[("PUT", path)]


def _connector(name="Microsoft 365"):
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "connector_type": "m365",
        "enabled": True,
        "scopes": _SCOPES,
        "created_at": _TS,
    }


def test_create_posts_connector():
    resp = _connector()
    client = DummyClient({("POST", "/connectors"): resp})
    service = ConnectorService(client)

    conn = service.create(
        ConnectorCreate(
            name="Microsoft 365",
            connector_type="m365",
            config={"tenant_id": "tenant-abc", "client_id": "client-xyz"},
            scopes=_SCOPES,
        )
    )

    assert conn.connector_type == "m365"
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "/connectors")
    body = kwargs["json"]
    assert body["connector_type"] == "m365"
    assert body["config"] == {"tenant_id": "tenant-abc", "client_id": "client-xyz"}
    assert body["name"] == "Microsoft 365"


def test_external_connector_aliases_are_back_compat():
    # Renamed schemas keep deprecated aliases so existing imports don't break.
    from kamiwaza_sdk.schemas.connectors import (
        Connector,
        ConnectorUpdate as CU,
        ExternalConnector,
        ExternalConnectorCreate,
        ExternalConnectorUpdate,
    )

    assert ExternalConnector is Connector
    assert ExternalConnectorCreate is ConnectorCreate
    assert ExternalConnectorUpdate is CU


def test_list_unwraps_items_envelope():
    client = DummyClient({("GET", "/connectors"): {"items": [_connector()]}})
    service = ConnectorService(client)

    out = service.list()

    assert len(out) == 1
    assert out[0].connector_type == "m365"


class _RaisingClient:
    """Raises a 404 APIError on get/put/delete to exercise NotFoundError mapping."""

    def get(self, path, **kwargs):
        raise APIError("not found", status_code=404)

    def put(self, path, **kwargs):
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


def test_subscribe_posts_manifest_and_endpoint():
    manifest = {
        "connector_type": "servicenow",
        "provider_id": "servicenow",
        "provider_label": "ServiceNow",
        "auth_model": {"kind": "service_token"},
    }
    resp = {
        "id": str(uuid.uuid4()),
        "name": "ServiceNow",
        "connector_type": "servicenow",
        "enabled": True,
        "scopes": [],
        "created_at": _TS,
    }
    client = DummyClient({("POST", "/connectors/subscriptions"): resp})
    service = ConnectorService(client)

    conn = service.subscribe(manifest=manifest, endpoint="https://sn.example/mcp")

    assert conn.connector_type == "servicenow"
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "/connectors/subscriptions")
    body = kwargs["json"]
    assert body["manifest"] == manifest
    assert body["endpoint"] == "https://sn.example/mcp"
    assert body["config"] == {}  # defaults to empty when no secret is given


def test_subscribe_sends_secret_config():
    manifest = {
        "connector_type": "servicenow",
        "provider_id": "servicenow",
        "provider_label": "ServiceNow",
        "auth_model": {"kind": "service_token"},
    }
    resp = {
        "id": str(uuid.uuid4()),
        "name": "ServiceNow",
        "connector_type": "servicenow",
        "enabled": True,
        "scopes": [],
        "created_at": _TS,
    }
    client = DummyClient({("POST", "/connectors/subscriptions"): resp})
    service = ConnectorService(client)

    service.subscribe(
        manifest=manifest,
        endpoint="https://sn.example/mcp",
        config={"service_token": "sk-sn-1"},
    )

    body = client.calls[0][2]["json"]
    assert body["config"] == {"service_token": "sk-sn-1"}


def test_subscribe_binds_workload_principal():
    """The minting workload principal is sent so the platform can bind it."""
    manifest = {
        "connector_type": "servicenow",
        "provider_id": "servicenow",
        "provider_label": "ServiceNow",
        "auth_model": {"kind": "service_token"},
    }
    resp = {
        "id": str(uuid.uuid4()),
        "name": "ServiceNow",
        "connector_type": "servicenow",
        "enabled": True,
        "scopes": [],
        "created_at": _TS,
    }
    client = DummyClient({("POST", "/connectors/subscriptions"): resp})
    service = ConnectorService(client)

    service.subscribe(
        manifest=manifest,
        endpoint="https://sn.example/mcp",
        workload_principal_id="sa-servicenow",
    )

    body = client.calls[0][2]["json"]
    assert body["workload_principal_id"] == "sa-servicenow"


def test_subscribe_sends_scopes():
    """Granted scopes are sent so a service_token connector can mint."""
    manifest = {
        "connector_type": "servicenow",
        "provider_id": "servicenow",
        "provider_label": "ServiceNow",
        "auth_model": {"kind": "service_token"},
    }
    resp = {
        "id": str(uuid.uuid4()),
        "name": "ServiceNow",
        "connector_type": "servicenow",
        "enabled": True,
        "scopes": ["incident.read"],
        "created_at": _TS,
    }
    client = DummyClient({("POST", "/connectors/subscriptions"): resp})
    service = ConnectorService(client)

    service.subscribe(
        manifest=manifest,
        endpoint="https://sn.example/mcp",
        scopes=["incident.read"],
    )

    body = client.calls[0][2]["json"]
    assert body["scopes"] == ["incident.read"]


def test_list_available_unwraps_items_envelope():
    item = {
        "id": str(uuid.uuid4()),
        "name": "Microsoft 365",
        "connector_type": "m365",
        "enabled": True,
        "scopes": _SCOPES,
    }
    client = DummyClient({("GET", "/connectors/available"): {"items": [item]}})
    service = ConnectorService(client)

    out = service.list_available()

    assert len(out) == 1
    assert out[0].connector_type == "m365"
    assert out[0].enabled is True
    assert client.calls[0][:2] == ("GET", "/connectors/available")


def test_update_sends_only_set_fields():
    cid = uuid.uuid4()
    resp = _connector(name="Renamed")
    resp["enabled"] = False
    client = DummyClient({("PUT", f"/connectors/{cid}"): resp})
    service = ConnectorService(client)

    conn = service.update(cid, ConnectorUpdate(enabled=False))

    assert conn.enabled is False
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("PUT", f"/connectors/{cid}")
    # exclude_none drops unset name/config/scopes; only enabled is sent.
    assert kwargs["json"] == {"enabled": False}


def test_update_maps_404_to_not_found():
    service = ConnectorService(_RaisingClient())
    with pytest.raises(NotFoundError):
        service.update(uuid.uuid4(), ConnectorUpdate(enabled=True))


def test_external_connector_aliases_preserve_old_names():
    """The pre-rename ExternalConnector* names still import and alias the new ones."""
    from kamiwaza_sdk.schemas.connectors import (
        Connector,
        ExternalConnector,
        ExternalConnectorCreate,
        ExternalConnectorUpdate,
    )

    assert ExternalConnector is Connector
    assert ExternalConnectorCreate is ConnectorCreate
    assert ExternalConnectorUpdate is ConnectorUpdate


def test_create_allows_empty_scopes():
    """A catalog/service-token connector with no OAuth scopes is creatable."""
    resp = _connector()
    resp["scopes"] = []
    client = DummyClient({("POST", "/connectors"): resp})
    service = ConnectorService(client)

    conn = service.create(
        ConnectorCreate(
            name="ServiceNow",
            connector_type="servicenow",
            config={"service_token_ref": "urn:secret:sn"},
        )
    )

    assert conn.connector_type == "m365"  # echoes the response connector_type
    body = client.calls[0][2]["json"]
    assert body["scopes"] == []
