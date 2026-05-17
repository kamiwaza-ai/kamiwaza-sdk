"""T7.10 / ENG-5044 — GatesAPI on the canonical kamiwaza_sdk surface.

WS-M3.2 test migration (T7.15 / ENG-5049). Drops the legacy
``kamiwaza.client.Kamiwaza`` + ``httpx_mock`` machinery in favor of the
canonical ``kamiwaza_sdk.services.gates.GatesAPI`` instantiated directly
against the shared ``MockClient`` fixture (conftest).

Customer-facing surface per design §4.2.11:

    kz.gates.discover(classpath)   -> GateDiscovery

Server-side correlate: POST /api/authz/gates/discover (§4.2.3).
"""

from __future__ import annotations


def test_discover_posts_to_server_with_classpath(mock_client) -> None:
    """kz.gates.discover(classpath) POSTs to /authz/gates/discover."""
    from kamiwaza_sdk.schemas.federation import GateDiscovery
    from kamiwaza_sdk.services.gates import GatesAPI

    mock_client.expect(
        "POST",
        "/authz/gates/discover",
        {
            "name": "AllowAllExecutionGate",
            "kind": "execution",
            "required_attributes": [],
            "config_schema": {},
            "classpath": (
                "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate"
            ),
            "location": "/.../default_gates.py:42",
        },
    )

    api = GatesAPI(client=mock_client)
    result = api.discover(
        "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate"
    )

    assert isinstance(result, GateDiscovery)
    assert result.kind == "execution"
    assert result.name == "AllowAllExecutionGate"


def test_discover_passes_classpath_in_request_body(mock_client) -> None:
    """The classpath is sent in the POST body — server's API contract."""
    from kamiwaza_sdk.services.gates import GatesAPI

    classpath = "my_policy.MyGate"

    mock_client.expect(
        "POST",
        "/authz/gates/discover",
        {
            "name": "my-gate",
            "kind": "execution",
            "required_attributes": [],
            "config_schema": {},
            "classpath": classpath,
            "location": "<unknown>",
        },
    )

    GatesAPI(client=mock_client).discover(classpath)

    method, path, kwargs = mock_client.calls[0]
    assert method == "POST"
    assert path == "/authz/gates/discover"
    assert kwargs.get("json") == {"classpath": classpath}


def test_discover_surfaces_required_attributes_and_schema(mock_client) -> None:
    """Customer-meaningful fields round-trip via the typed model."""
    from kamiwaza_sdk.services.gates import GatesAPI

    mock_client.expect(
        "POST",
        "/authz/gates/discover",
        {
            "name": "classification-gate",
            "kind": "execution",
            "required_attributes": [
                {"name": "clearance", "kind": "string"},
                {"name": "country", "kind": "string"},
            ],
            "config_schema": {
                "type": "object",
                "properties": {"min_clearance": {"type": "string"}},
            },
            "classpath": "stub.ClassificationGate",
            "location": "<stub>",
        },
    )

    result = GatesAPI(client=mock_client).discover("stub.ClassificationGate")

    assert len(result.required_attributes) == 2
    assert result.required_attributes[0]["name"] == "clearance"
    assert result.config_schema["properties"]["min_clearance"]["type"] == "string"
