"""ENG-4946 / M3.1 — attribute-schema SDK tests on canonical surface.

WS-M3.2 test migration (T7.15 / ENG-5049). Covers the §4.2.18 declared
vocabulary surface on ``kamiwaza_sdk.services.cluster_federation.ClusterAPI``:

    kz.cluster.declare_attribute(name, *, type, sensitive, authority, schema_version)
        -> AttributeSchema  (PUT /api/cluster/attribute-schema/{name})
    kz.cluster.list_attributes(*, include_deprecated)
        -> list[AttributeSchema]  (GET /api/cluster/attribute-schema)
    kz.cluster.deprecate_attribute(name)
        -> AttributeSchema  (DELETE + GET /api/cluster/attribute-schema/{name})
    kz.cluster.withdraw_attribute(name, *, force, subjects_holding_value)
        -> dict  (DELETE /api/cluster/attribute-schema/{name}?force=true)

PR-feedback M6 (test coverage gap): concurrent-withdraw 404 fallback on
``deprecate_attribute`` is covered by the new
``test_deprecate_attribute_404_on_followup_get_raises_kamiwaza_error``.
"""

from __future__ import annotations

from datetime import datetime, timezone

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


def test_declare_attribute_puts_to_cluster_endpoint(mock_client) -> None:
    from kamiwaza_sdk.schemas.federation import AttributeSchema
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "PUT",
        "/cluster/attribute-schema/clearance",
        _attribute_schema_payload("clearance"),
    )

    schema = ClusterAPI(client=mock_client).declare_attribute(
        "clearance", type="string"
    )

    assert isinstance(schema, AttributeSchema)
    assert schema.name == "clearance"
    assert schema.type == "string"
    assert schema.state == "declared"
    assert schema.authority == "local_admin"
    assert schema.sensitive is False

    method, path, kwargs = mock_client.calls[0]
    assert method == "PUT"
    assert path == "/cluster/attribute-schema/clearance"
    assert kwargs.get("json") == {
        "type": "string",
        "sensitive": False,
        "authority": "local_admin",
        "schema_version": "1.0",
    }


def test_declare_attribute_forwards_governance_fields(mock_client) -> None:
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "PUT",
        "/cluster/attribute-schema/ssn_last4",
        {
            **_attribute_schema_payload("ssn_last4", sensitive=True),
            "authority": "mesh_peer",
            "schema_version": "2.0",
        },
    )

    schema = ClusterAPI(client=mock_client).declare_attribute(
        "ssn_last4",
        type="string",
        sensitive=True,
        authority="mesh_peer",
        schema_version="2.0",
    )

    assert schema.sensitive is True
    assert schema.authority == "mesh_peer"
    assert schema.schema_version == "2.0"

    _method, _path, kwargs = mock_client.calls[0]
    body = kwargs.get("json", {})
    assert body["sensitive"] is True
    assert body["authority"] == "mesh_peer"
    assert body["schema_version"] == "2.0"


def test_declare_attribute_multivalued(mock_client) -> None:
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "PUT",
        "/cluster/attribute-schema/programs",
        _attribute_schema_payload("programs", type_="string[]"),
    )

    schema = ClusterAPI(client=mock_client).declare_attribute(
        "programs", type="string[]"
    )
    assert schema.type == "string[]"

    _method, _path, kwargs = mock_client.calls[0]
    assert kwargs.get("json", {})["type"] == "string[]"


def test_list_attributes_returns_pydantic_list(mock_client) -> None:
    from kamiwaza_sdk.schemas.federation import AttributeSchema
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "GET",
        "/cluster/attribute-schema",
        {
            "attributes": [
                _attribute_schema_payload("clearance"),
                _attribute_schema_payload("country", state="deprecated"),
            ],
            "schema_version": "v0.3.6",
        },
    )

    schemas = ClusterAPI(client=mock_client).list_attributes()

    assert len(schemas) == 2
    assert all(isinstance(s, AttributeSchema) for s in schemas)
    names = {s.name for s in schemas}
    assert names == {"clearance", "country"}

    _method, _path, kwargs = mock_client.calls[0]
    assert kwargs.get("params") == {"include_deprecated": "true"}


def test_list_attributes_passes_include_deprecated_false(mock_client) -> None:
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "GET",
        "/cluster/attribute-schema",
        {"attributes": [], "schema_version": "v0.3.6"},
    )

    schemas = ClusterAPI(client=mock_client).list_attributes(include_deprecated=False)
    assert schemas == []

    _method, _path, kwargs = mock_client.calls[0]
    assert kwargs.get("params") == {"include_deprecated": "false"}


def test_deprecate_attribute_round_trips_via_get(mock_client) -> None:
    """H4 (PR feedback): deprecate_attribute() reads the full schema back
    via a single-name GET (not list_attributes) — smaller race window."""
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "DELETE",
        "/cluster/attribute-schema/clearance",
        {"state": "deprecated", "subjects_holding_value": 0},
    )
    mock_client.expect(
        "GET",
        "/cluster/attribute-schema/clearance",
        _attribute_schema_payload("clearance", state="deprecated"),
    )

    schema = ClusterAPI(client=mock_client).deprecate_attribute("clearance")

    assert schema.state == "deprecated"
    assert schema.name == "clearance"


def test_deprecate_attribute_404_on_followup_get_raises_kamiwaza_error(
    mock_client,
) -> None:
    """PR-feedback M6: when a concurrent ``withdraw_attribute`` removes the
    schema between the DELETE and the GET roundtrip, the SDK surfaces a
    clear ``KamiwazaError`` instead of synthesizing a fake schema — the
    operator needs to know the schema is gone, not that it's deprecated.
    """
    from kamiwaza_sdk.exceptions import KamiwazaError
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "DELETE",
        "/cluster/attribute-schema/clearance",
        {"state": "deprecated", "subjects_holding_value": 0},
    )
    mock_client.raise_on(
        "GET",
        "/cluster/attribute-schema/clearance",
        KamiwazaError("attribute withdrawn", status_code=404),
    )

    with pytest.raises(KamiwazaError) as exc_info:
        ClusterAPI(client=mock_client).deprecate_attribute("clearance")

    msg = str(exc_info.value)
    # Surface message should mention the race / suggest re-fetching state.
    assert "concurrent" in msg.lower() or "withdraw" in msg.lower()


def test_withdraw_attribute_passes_force_param(mock_client) -> None:
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "DELETE",
        "/cluster/attribute-schema/clearance",
        {"state": "withdrawn", "subjects_holding_value": 5},
    )

    result = ClusterAPI(client=mock_client).withdraw_attribute(
        "clearance", force=True, subjects_holding_value=5
    )

    assert result == {"state": "withdrawn", "subjects_holding_value": 5}
    _method, _path, kwargs = mock_client.calls[0]
    assert kwargs.get("params") == {"force": "true", "subjects_holding_value": 5}


def test_withdraw_attribute_default_no_force(mock_client) -> None:
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "DELETE",
        "/cluster/attribute-schema/clearance",
        {"state": "deprecated", "subjects_holding_value": 0},
    )

    result = ClusterAPI(client=mock_client).withdraw_attribute("clearance")

    assert result["state"] == "deprecated"
    _method, _path, kwargs = mock_client.calls[0]
    assert kwargs.get("params") == {"force": "false", "subjects_holding_value": 0}


def test_declare_attribute_400_raises_kamiwaza_error(mock_client) -> None:
    """Server 4xx → SDK raises KamiwazaError (per the exception mapping)."""
    from kamiwaza_sdk.exceptions import KamiwazaError
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.raise_on(
        "PUT",
        "/cluster/attribute-schema/clearance",
        KamiwazaError(
            "shape_change_on_declared",
            status_code=400,
            body={
                "detail": {
                    "reason": "shape_change_on_declared",
                    "name": "clearance",
                    "conflict": {"type": {"existing": "string", "target": "int"}},
                }
            },
        ),
    )

    with pytest.raises(KamiwazaError):
        ClusterAPI(client=mock_client).declare_attribute("clearance", type="int")
