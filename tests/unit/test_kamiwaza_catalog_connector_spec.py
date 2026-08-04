"""Unit tests for the connector-spec SDK bindings (ENG-6964).

Covers ``catalog.register_from_spec`` + publisher grant/revoke and the typed
``ConnectorSpec`` schema. HTTP is mocked — no live cluster.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.connector_spec import ConnectorSpec
from kamiwaza_sdk.services.catalog import CatalogService, DatasetClient

pytestmark = pytest.mark.unit


def _spec_dict() -> dict:
    return {
        "platform": "kamiwaza",
        "base_url": "https://example.test/api",
        "endpoint": {"method": "GET", "path": "/models", "items_path": "items"},
        "index": "platform-models",
        "pagination": {"max_pages": 1, "page_size": 100},
        "auth": {"kind": "bearer", "credential_ref": "urn:li:secret:demo"},
        "data_attribute_fields": ["data_class"],
        "gate": {
            "type": "kamiwaza.services.authz.gates.attribute_gate.AttributeGate",
            "config": {"data_class_field": "data_class"},
        },
    }


# --------------------------------------------------------------------------- #
# ConnectorSpec schema
# --------------------------------------------------------------------------- #
def test_connector_spec_builds_and_dumps_expected_keys():
    body = ConnectorSpec(**_spec_dict()).model_dump(exclude_none=True, mode="json")
    assert body["spec_version"] == "connector-spec.v1"
    assert body["platform"] == "kamiwaza"
    assert body["endpoint"]["method"] == "GET"
    assert body["endpoint"]["items_path"] == "items"
    assert body["pagination"]["page_size"] == 100
    assert body["auth"]["kind"] == "bearer"
    assert body["data_attribute_fields"] == ["data_class"]
    # None-valued optionals are excluded so the engine sees its own defaults.
    assert "time_field" not in body
    assert "field_mappings" not in body


# --------------------------------------------------------------------------- #
# register_from_spec
# --------------------------------------------------------------------------- #
def test_register_from_spec_posts_dict_and_returns_urn():
    client = Mock()
    client.post.return_value = {"dataset_urn": "urn:li:dataset:(x,platform-models,PROD)"}
    urn = DatasetClient(client).register_from_spec(_spec_dict())
    assert urn == "urn:li:dataset:(x,platform-models,PROD)"
    args, kwargs = client.post.call_args
    assert args[0] == "/catalog/datasets/register-from-spec"
    assert kwargs["json"]["index"] == "platform-models"


def test_register_from_spec_accepts_typed_model_via_facade():
    client = Mock()
    client.post.return_value = {"dataset_urn": "urn:li:dataset:abc"}
    urn = CatalogService(client).register_from_spec(ConnectorSpec(**_spec_dict()))
    assert urn == "urn:li:dataset:abc"
    _, kwargs = client.post.call_args
    assert kwargs["json"]["spec_version"] == "connector-spec.v1"
    assert kwargs["json"]["auth"]["credential_ref"] == "urn:li:secret:demo"


def test_register_from_spec_unwraps_bare_string_response():
    client = Mock()
    client.post.return_value = "urn:li:dataset:bare"
    assert DatasetClient(client).register_from_spec(_spec_dict()) == "urn:li:dataset:bare"


def test_register_from_spec_raises_on_dict_without_urn():
    """A dict response carrying no URN key fails loudly, not silently."""
    client = Mock()
    client.post.return_value = {"unexpected": "shape"}
    with pytest.raises(APIError):
        DatasetClient(client).register_from_spec(_spec_dict())


# --------------------------------------------------------------------------- #
# publisher grant / revoke
# --------------------------------------------------------------------------- #
def test_add_publisher_posts_subject_user_id():
    client = Mock()
    CatalogService(client).add_publisher("urn:li:corpuser:steward")
    args, kwargs = client.post.call_args
    assert args[0] == "/catalog/datasets/publishers"
    assert kwargs["json"] == {"subject_user_id": "urn:li:corpuser:steward"}


def test_remove_publisher_deletes_with_subject_body():
    client = Mock()
    CatalogService(client).remove_publisher("urn:li:corpuser:steward")
    args, kwargs = client.delete.call_args
    assert args[0] == "/catalog/datasets/publishers"
    assert kwargs["json"] == {"subject_user_id": "urn:li:corpuser:steward"}
