from __future__ import annotations

import pytest

from kamiwaza_sdk.services.cluster import ClusterService

pytestmark = pytest.mark.unit


def test_capabilities_accepts_release_payload_without_newer_fields(dummy_client):
    responses = {
        ("get", "/cluster/cluster_capabilities"): {
            "available_platforms": ["Fast CPU"],
            "cluster_ip": "10.244.0.23",
            "gpu_count": 0,
            "gpu_inventory_source": "local",
            "gpu_types": [],
            "gpus": [],
            "hostname": "core-raycluster-head",
            "local_node_id": "6abfb129-5473-47fe-8a21-c135f23f9ad7",
            "ray_node_id": "058f081ec4af8347f804860d83fc119c8c5391534a3ecee28b5f631f",
        },
    }
    service = ClusterService(dummy_client(responses))

    result = service.capabilities()

    assert result.gpu_count == 0
    assert result.federation_count == 0
    assert result.ray_ready is False
    assert result.available_platforms == ["Fast CPU"]
