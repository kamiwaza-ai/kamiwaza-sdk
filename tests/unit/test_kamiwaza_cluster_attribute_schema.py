"""ENG-4946 / M3.1 — kamiwaza.cluster attribute-schema SDK tests.

Covers the §4.2.18 declared-vocabulary surface:

    kz.cluster.declare_attribute(name, *, type, sensitive, authority, schema_version)
        -> AttributeSchema  (PUT /api/cluster/attribute-schema/{name})
    kz.cluster.list_attributes(*, include_deprecated)
        -> list[AttributeSchema]  (GET /api/cluster/attribute-schema)
    kz.cluster.deprecate_attribute(name)
        -> AttributeSchema  (DELETE /api/cluster/attribute-schema/{name})
    kz.cluster.withdraw_attribute(name, *, force, subjects_holding_value)
        -> dict  (DELETE /api/cluster/attribute-schema/{name}?force=true)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest


def _attribute_schema_payload(
    name: str,
    type_: str = "string",
    state: str = "declared",
    sensitive: bool = False,
) -> dict:
    return {
        "name": name,
        "type": type_,
        "state": state,
        "authority": "local_admin",
        "sensitive": sensitive,
        "schema_version": "1.0",
        "declared_at": datetime(2026, 5, 12, tzinfo=timezone.utc).isoformat(),
    }


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_declare_attribute_puts_to_cluster_endpoint(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import AttributeSchema

    httpx_mock.add_response(
        method="PUT",
        url="https://kamiwaza.test/api/cluster/attribute-schema/clearance",
        status_code=200,
        json=_attribute_schema_payload("clearance"),
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    schema = client.cluster.declare_attribute("clearance", type="string")

    assert isinstance(schema, AttributeSchema)
    assert schema.name == "clearance"
    assert schema.type == "string"
    assert schema.state == "declared"
    assert schema.authority == "local_admin"
    assert schema.sensitive is False

    request = httpx_mock.get_requests(method="PUT")[0]
    body = json.loads(request.content)
    assert body == {
        "type": "string",
        "sensitive": False,
        "authority": "local_admin",
        "schema_version": "1.0",
    }


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_declare_attribute_forwards_governance_fields(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="PUT",
        url="https://kamiwaza.test/api/cluster/attribute-schema/ssn_last4",
        status_code=200,
        json={
            **_attribute_schema_payload("ssn_last4", sensitive=True),
            "authority": "mesh_peer",
            "schema_version": "2.0",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    schema = client.cluster.declare_attribute(
        "ssn_last4",
        type="string",
        sensitive=True,
        authority="mesh_peer",
        schema_version="2.0",
    )

    assert schema.sensitive is True
    assert schema.authority == "mesh_peer"
    assert schema.schema_version == "2.0"

    request = httpx_mock.get_requests(method="PUT")[0]
    body = json.loads(request.content)
    assert body["sensitive"] is True
    assert body["authority"] == "mesh_peer"
    assert body["schema_version"] == "2.0"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_declare_attribute_multivalued(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="PUT",
        url="https://kamiwaza.test/api/cluster/attribute-schema/programs",
        status_code=200,
        json=_attribute_schema_payload("programs", type_="string[]"),
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    schema = client.cluster.declare_attribute("programs", type="string[]")
    assert schema.type == "string[]"

    request = httpx_mock.get_requests(method="PUT")[0]
    body = json.loads(request.content)
    assert body["type"] == "string[]"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_list_attributes_returns_pydantic_list(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import AttributeSchema

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/attribute-schema?include_deprecated=true",
        status_code=200,
        json={
            "attributes": [
                _attribute_schema_payload("clearance"),
                _attribute_schema_payload("country", state="deprecated"),
            ],
            "schema_version": "v0.3.6",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    schemas = client.cluster.list_attributes()

    assert len(schemas) == 2
    assert all(isinstance(s, AttributeSchema) for s in schemas)
    names = {s.name for s in schemas}
    assert names == {"clearance", "country"}


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_list_attributes_passes_include_deprecated_false(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/attribute-schema?include_deprecated=false",
        status_code=200,
        json={"attributes": [], "schema_version": "v0.3.6"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    schemas = client.cluster.list_attributes(include_deprecated=False)
    assert schemas == []


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_deprecate_attribute_round_trips_via_list(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="DELETE",
        url="https://kamiwaza.test/api/cluster/attribute-schema/clearance",
        status_code=200,
        json={"state": "deprecated", "subjects_holding_value": 0},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/attribute-schema?include_deprecated=true",
        status_code=200,
        json={
            "attributes": [_attribute_schema_payload("clearance", state="deprecated")],
            "schema_version": "v0.3.6",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    schema = client.cluster.deprecate_attribute("clearance")

    assert schema.state == "deprecated"
    assert schema.name == "clearance"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_withdraw_attribute_passes_force_param(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="DELETE",
        url=(
            "https://kamiwaza.test/api/cluster/attribute-schema/clearance"
            "?force=true&subjects_holding_value=5"
        ),
        status_code=200,
        json={"state": "withdrawn", "subjects_holding_value": 5},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.cluster.withdraw_attribute(
        "clearance", force=True, subjects_holding_value=5
    )

    assert result == {"state": "withdrawn", "subjects_holding_value": 5}


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_withdraw_attribute_default_no_force(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="DELETE",
        url=(
            "https://kamiwaza.test/api/cluster/attribute-schema/clearance"
            "?force=false&subjects_holding_value=0"
        ),
        status_code=200,
        json={"state": "deprecated", "subjects_holding_value": 0},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.cluster.withdraw_attribute("clearance")

    assert result["state"] == "deprecated"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_declare_attribute_404_raises_kamiwaza_error(httpx_mock: Any) -> None:
    """Server 4xx → SDK raises KamiwazaError (per the exception mapping)."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import KamiwazaError

    httpx_mock.add_response(
        method="PUT",
        url="https://kamiwaza.test/api/cluster/attribute-schema/clearance",
        status_code=400,
        json={
            "detail": {
                "reason": "shape_change_on_declared",
                "name": "clearance",
                "conflict": {"type": {"existing": "string", "target": "int"}},
            }
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    with pytest.raises(KamiwazaError):
        client.cluster.declare_attribute("clearance", type="int")
