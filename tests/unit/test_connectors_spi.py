"""The connector server SPI (``kamiwaza_sdk.connectors``).

A connector pod subclasses :class:`ConnectorProvider`, declares a
:class:`ConfigSchema`, and serves it with :func:`create_connector_app`. These
assert the contract connector repos depend on (``kamiwaza-sdk[connector]``):
``to_manifest()`` shape, config validation, and the served ``GET /manifest``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kamiwaza_sdk.connectors import (
    ConfigField,
    ConfigSchema,
    ConfigType,
    ConnectorProvider,
    InvalidConfigException,
    create_connector_app,
)


class _DummyProvider(ConnectorProvider):
    @property
    def connector_type(self) -> str:
        return "dummy"

    def config_schema(self) -> ConfigSchema:
        return ConfigSchema(
            fields=(
                ConfigField(name="client_id", type=ConfigType.STRING),
                ConfigField(name="client_secret", type=ConfigType.STRING, secret=True),
            )
        )


def test_to_manifest_carries_identity_and_config_schema() -> None:
    manifest = _DummyProvider().to_manifest()
    assert manifest["connector_type"] == "dummy"
    cfg = manifest["config_schema"]
    assert cfg["type"] == "object"
    assert set(cfg["required"]) == {"client_id", "client_secret"}
    assert cfg["properties"]["client_secret"]["writeOnly"] is True


def test_validate_config_rejects_missing_required() -> None:
    provider = _DummyProvider()
    provider.validate_config({"client_id": "abc", "client_secret": "shh"})  # ok
    with pytest.raises(InvalidConfigException):
        provider.validate_config({"client_id": "abc"})


def test_create_connector_app_serves_manifest() -> None:
    app = create_connector_app(
        title="kamiwaza-connector-dummy",
        provider=_DummyProvider(),
        build_dispatcher=lambda: None,
        error_type=Exception,
        classify_error=lambda exc: (500, "internal", None),
        dispatcher=object(),  # injected so the lifespan skips env construction
    )
    with TestClient(app) as client:
        resp = client.get("/manifest")
    assert resp.status_code == 200
    assert resp.json()["connector_type"] == "dummy"
