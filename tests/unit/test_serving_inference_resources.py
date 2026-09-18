from uuid import uuid4

import pytest

from kamiwaza_sdk.schemas.serving.serving import (
    AcceleratorInferenceRequest,
    CpuResourceQuantities,
    CreateModelDeployment,
)
from kamiwaza_sdk.services.serving import ServingService


pytestmark = pytest.mark.unit


def test_cpu_quantity_rejects_decimal_overflow():
    with pytest.raises(ValueError, match="supported quantity range"):
        CpuResourceQuantities(cpu="1e999999999", memory="1Gi")


def test_list_deployments_round_trips_accelerator_inference_resources(mock_client):
    """A tenant GPU deployment returned by core must be parseable by the SDK."""
    deployment_id = uuid4()
    payload = {
        "id": str(deployment_id),
        "m_id": str(uuid4()),
        "m_config_id": str(uuid4()),
        "requested_at": "2026-06-09T00:00:00Z",
        "status": "DEPLOYED",
        "instances": [],
        "inferenceResources": {
            "schemaVersion": 1,
            "accelerator": {
                "capability": "gpu",
                "count": 1,
                "memory": {"minimum": "20Gi"},
                "isolation": "any-qualified",
                "profile": "amd-whole-gpu-rdna",
            },
            "runtime": {"selection": "automatic"},
            "alternatives": [],
        },
    }
    mock_client.expect("GET", "/serving/deployments", [payload])

    deployments = ServingService(mock_client).list_deployments()

    assert isinstance(deployments[0].inference_resources, AcceleratorInferenceRequest)
    assert deployments[0].inference_resources.accelerator.profile == "amd-whole-gpu-rdna"


def test_estimate_model_vram_uses_wire_aliases_for_inference_resources(mock_client):
    request = CreateModelDeployment(
        m_id=uuid4(),
        m_config_id=uuid4(),
        inferenceResources={
            "schemaVersion": 1,
            "accelerator": {
                "capability": "gpu",
                "count": 1,
                "memory": {"minimum": "20Gi"},
                "isolation": "any-qualified",
                "profile": "nvidia-whole",
            },
            "runtime": {"selection": "automatic"},
            "alternatives": [],
        },
    )
    mock_client.expect(
        "POST",
        "/serving/estimate_model_vram",
        {"computed_vram_estimate": 1},
    )

    result = ServingService(mock_client).estimate_model_vram(request)

    assert result["computed_vram_estimate"] == 1
    payload = mock_client.calls[-1][2]["json"]
    assert payload["inferenceResources"]["schemaVersion"] == 1
    assert "inference_resources" not in payload
    assert "schema_version" not in payload["inferenceResources"]


def test_response_inference_resources_preserve_additive_server_fields(mock_client):
    deployment_id = uuid4()
    payload = {
        "id": str(deployment_id),
        "m_id": str(uuid4()),
        "m_config_id": str(uuid4()),
        "requested_at": "2026-06-09T00:00:00Z",
        "status": "DEPLOYED",
        "instances": [],
        "inferenceResources": {
            "schemaVersion": 1,
            "accelerator": {
                "capability": "gpu",
                "count": 1,
                "memory": {"minimum": "20Gi", "ownerHint": "future"},
                "isolation": "any-qualified",
                "profile": "nvidia-whole",
                "allocationClass": "future",
            },
            "runtime": {"selection": "automatic", "engineHint": "future"},
            "alternatives": [],
            "serverRevision": 2,
        },
    }
    mock_client.expect("GET", "/serving/deployments", [payload])

    deployment = ServingService(mock_client).list_deployments()[0]

    resources = deployment.inference_resources
    assert isinstance(resources, AcceleratorInferenceRequest)
    assert resources.model_extra == {"serverRevision": 2}
    assert resources.accelerator.model_extra == {"allocationClass": "future"}
    assert resources.accelerator.memory.model_extra == {"ownerHint": "future"}
    assert resources.runtime.model_extra == {"engineHint": "future"}
