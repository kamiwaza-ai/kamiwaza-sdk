"""T5.21 / ENG-4698 — kamiwaza.cluster module + federations[].probe() tests.

Customer-facing capabilities probe surface per design §4.2.11 / §4.4.3:

    kz.cluster.capabilities()              -> ClusterCapabilities  (local)
    kz.federations[name].probe()           -> ClusterCapabilities  (via mesh)

The local path hits ``GET /api/cluster/cluster_capabilities`` on the local
cluster (auth widened to viewer by ENG-4697); the mesh path hits
``GET /api/mesh/{name}/api/cluster/cluster_capabilities`` so a probing peer
sees the remote cluster's GPU count, active deployments, Ray status, and
federation count (extended by ENG-4696).

Demo bullet (4): ``kz.federations["ORION"].probe()`` returns ORION's
capabilities.
"""

from __future__ import annotations

from typing import Any

import pytest


def test_kamiwaza_exposes_cluster_attribute() -> None:
    """``client.cluster`` is the entry point for cluster operations."""
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    assert client.cluster is not None


def test_cluster_is_lazy_loaded() -> None:
    """Per .ai/rules/sdk-patterns.md, services are lazy-loaded."""
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    a = client.cluster
    b = client.cluster
    assert a is b


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_capabilities_hits_local_endpoint(httpx_mock: Any) -> None:
    """``kz.cluster.capabilities()`` GETs ``/api/cluster/cluster_capabilities``."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import ClusterCapabilities

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/cluster_capabilities",
        status_code=200,
        json={
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

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    caps = client.cluster.capabilities()

    assert isinstance(caps, ClusterCapabilities)
    assert caps.gpu_count == 1
    assert caps.federation_count == 2
    assert caps.active_deployments == 3
    assert caps.ray_ready is True


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_capabilities_allows_unknown_fields(httpx_mock: Any) -> None:
    """Forward-compat: new server-side fields must not crash the SDK."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/cluster_capabilities",
        status_code=200,
        json={
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

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    caps = client.cluster.capabilities()

    # Known fields validate; unknown fields pass through via extra="allow".
    assert caps.system_type == "apple"
    raw = caps.model_dump()
    assert raw["future_field_we_do_not_know"] == {"nested": "ok"}


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_federation_probe_hits_mesh_routed_endpoint(httpx_mock: Any) -> None:
    """``kz.federations["ORION"].probe()`` GETs through ``/api/mesh/ORION/...``.

    Per design §4.4.3: probing a peer's capabilities goes through the mesh
    proxy. The selector is the federation's remote_cluster_name.
    """
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import ClusterCapabilities

    httpx_mock.add_response(
        method="GET",
        url=("https://kamiwaza.test/api/mesh/ORION/api/cluster/cluster_capabilities"),
        status_code=200,
        json={
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

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    caps = client.federations["ORION"].probe()

    assert isinstance(caps, ClusterCapabilities)
    assert caps.gpu_count == 8
    assert caps.active_deployments == 5
    assert caps.ray_ready is True


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_probe_does_not_resolve_federation_id(httpx_mock: Any) -> None:
    """Probe must not trigger a federations list lookup — selector is the
    cluster name itself, used directly in the mesh path. This protects the
    probe latency budget and avoids an unnecessary round-trip."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="GET",
        url=("https://kamiwaza.test/api/mesh/LYRA/api/cluster/cluster_capabilities"),
        status_code=200,
        json={
            "system_type": "linux",
            "os": "Linux",
            "gpu_count": 0,
            "available_platforms": ["Fast CPU"],
            "federation_count": 1,
            "active_deployments": 0,
            "ray_ready": True,
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    proxy = client.federations["LYRA"]
    caps = proxy.probe()

    # Confirm only the mesh call was issued (no GET /api/cluster/federations
    # for id resolution). httpx_mock would surface an unexpected call as a
    # MissingResponseError on teardown.
    assert caps.gpu_count == 0
