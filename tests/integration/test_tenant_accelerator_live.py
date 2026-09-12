"""Live qualification for explicit tenant accelerator requests."""

from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError
from model_targets import GGUF_LLM_TARGET


pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
]


def test_explicit_amd_accelerator_request_uses_owner_profile(
    live_kamiwaza_client, ensure_repo_ready, target_model_file_id
):
    """An explicit logical GPU request deploys through the owner profile path."""
    client = live_kamiwaza_client
    try:
        model = ensure_repo_ready(
            client,
            GGUF_LLM_TARGET.repo_id,
            quantization=GGUF_LLM_TARGET.quantization,
        )
    except (TimeoutError, RuntimeError, ValueError) as exc:
        pytest.skip(f"AMD model preparation unavailable: {type(exc).__name__}: {exc}")
    except APIError as exc:
        if getattr(exc, "status_code", 0) >= 500:
            pytest.skip(f"AMD model preparation unavailable: APIError {exc}")
        raise

    configs = client.models.get_model_configs(model.id)
    if not configs:
        pytest.skip("No model config available for explicit AMD accelerator test")
    config = next((item for item in configs if item.default), configs[0])
    deployment_id = None
    try:
        try:
            deployment_id = client.serving.deploy_model(
                model_id=str(model.id),
                m_config_id=config.id,
                m_file_id=target_model_file_id(model, GGUF_LLM_TARGET.quantization),
                engine_name="llamacpp",
                inferenceResources={
                    "schemaVersion": 1,
                    "accelerator": {
                        "capability": "gpu",
                        "count": 1,
                        "memory": {"minimum": "4Gi"},
                        "isolation": "any-qualified",
                        "profile": "amd-whole-gpu-rdna",
                    },
                    "runtime": {"selection": "automatic"},
                    "alternatives": [],
                },
                lb_port=0,
                autoscaling=False,
                min_copies=1,
                starting_copies=1,
                wait=False,
            )
        except APIError as exc:
            if getattr(exc, "status_code", 0) >= 500:
                pytest.skip(f"AMD accelerator deployment unavailable: APIError {exc}")
            raise
        if not deployment_id:
            pytest.skip("AMD accelerator deployment was refused by the host")

        details = client.serving.wait_for_deployment(
            deployment_id, poll_interval=5, timeout=600
        )
        assert details.instances, "AMD accelerator deployment should report instances"
        assert details.inference_resources is not None
        assert details.inference_resources.accelerator.profile == "amd-whole-gpu-rdna"
    finally:
        if deployment_id:
            try:
                client.serving.stop_deployment(deployment_id=deployment_id, force=True)
            except Exception:  # noqa: BLE001 - cleanup must not hide test result
                pass
