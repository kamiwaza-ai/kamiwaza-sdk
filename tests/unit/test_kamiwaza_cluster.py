"""T5.21 / ENG-4698 — ClusterAPI.capabilities + FederationProxy.probe.

WS-M3.2 test migration (T7.15 / ENG-5049). Customer-facing capabilities
probe surface per design §4.2.11 / §4.4.3:

    kz.cluster.capabilities()        -> ClusterCapabilities  (local)
    kz.federations[name].probe()     -> ClusterCapabilities  (via mesh)
"""

from __future__ import annotations


def test_capabilities_hits_local_endpoint(mock_client) -> None:
    """``capabilities()`` GETs ``/cluster/cluster_capabilities``."""
    from kamiwaza_sdk.schemas.federation import ClusterCapabilities
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "GET",
        "/cluster/cluster_capabilities",
        {
            "system_type": "linux",
            "os": "Linux",
            "gpu_count": 1,
            "gpu_types": ["nvidia"],
            "available_platforms": ["GPU", "Fast CPU"],
            "federation_count": 2,
            "active_deployments": 3,
            "ray_ready": True,
            "is_dgx_spark": False,
        },
    )

    caps = ClusterAPI(client=mock_client).capabilities()

    assert isinstance(caps, ClusterCapabilities)
    assert caps.gpu_count == 1
    assert caps.federation_count == 2
    assert caps.active_deployments == 3
    assert caps.ray_ready is True


def test_capabilities_allows_unknown_fields(mock_client) -> None:
    """Forward-compat: new server-side fields must not crash the SDK."""
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "GET",
        "/cluster/cluster_capabilities",
        {
            "system_type": "apple",
            "os": "Darwin",
            "gpu_count": 0,
            "available_platforms": ["Apple Silicon", "Fast CPU"],
            "federation_count": 0,
            "active_deployments": 0,
            "ray_ready": False,
            "future_field_we_do_not_know": {"nested": "ok"},
        },
    )

    caps = ClusterAPI(client=mock_client).capabilities()

    assert caps.system_type == "apple"
    raw = caps.model_dump()
    assert raw["future_field_we_do_not_know"] == {"nested": "ok"}


def test_federation_probe_hits_mesh_routed_endpoint(mock_client) -> None:
    """``federations["ORION"].probe()`` GETs ``/mesh/ORION/api/cluster/cluster_capabilities``."""
    from kamiwaza_sdk.schemas.federation import ClusterCapabilities
    from kamiwaza_sdk.services.federations import FederationsAPI

    mock_client.expect(
        "GET",
        "/mesh/ORION/api/cluster/cluster_capabilities",
        {
            "system_type": "linux",
            "os": "Linux",
            "gpu_count": 8,
            "gpu_types": ["nvidia"],
            "available_platforms": ["GPU", "Fast CPU"],
            "federation_count": 1,
            "active_deployments": 5,
            "ray_ready": True,
        },
    )

    caps = FederationsAPI(client=mock_client)["ORION"].probe()

    assert isinstance(caps, ClusterCapabilities)
    assert caps.gpu_count == 8
    assert caps.active_deployments == 5
    assert caps.ray_ready is True


def test_probe_does_not_resolve_federation_id(mock_client) -> None:
    """Probe must not trigger a federations list lookup — selector is the
    cluster name itself. Mocking only the mesh call means any spurious
    list call surfaces as ``AssertionError: no expectation set``."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    mock_client.expect(
        "GET",
        "/mesh/LYRA/api/cluster/cluster_capabilities",
        {
            "system_type": "linux",
            "os": "Linux",
            "gpu_count": 0,
            "available_platforms": ["Fast CPU"],
            "federation_count": 1,
            "active_deployments": 0,
            "ray_ready": True,
        },
    )

    proxy = FederationsAPI(client=mock_client)["LYRA"]
    caps = proxy.probe()

    assert caps.gpu_count == 0
    # Confirm only the mesh call was issued.
    assert all(
        path == "/mesh/LYRA/api/cluster/cluster_capabilities"
        for _method, path, _kwargs in mock_client.calls
    )
