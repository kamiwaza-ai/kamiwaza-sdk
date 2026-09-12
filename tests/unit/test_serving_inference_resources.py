from uuid import uuid4

import pytest

from kamiwaza_sdk.schemas.serving.serving import AcceleratorInferenceRequest
from kamiwaza_sdk.services.serving import ServingService


pytestmark = pytest.mark.unit


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
