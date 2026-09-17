"""Shared fixtures for validation-provider contract tests."""

from __future__ import annotations


def profile_payload() -> dict[str, object]:
    return {
        "schema": "kamiwaza.validation-profile/v1",
        "deployment": {
            "provider": "existing-host",
            "topology_id": "single-node-amd",
            "ephemeral": False,
        },
        "clusters": [
            {
                "id": "evo-x2-2",
                "roles": ["controller", "inference"],
                "node_count": 1,
                "hardware": {
                    "accelerators": [
                        {
                            "vendor": "amd",
                            "architecture": "gfx1151",
                            "count": 1,
                        }
                    ]
                },
                "features": {"rebac": True},
            }
        ],
        "mesh": {"edges": []},
        "validation": {
            "level": "smoke",
            "fixture_mode": "owned",
            "include": ["sdk.inference.lifecycle/v1"],
            "exclude": [],
        },
        "inference_targets": [
            {
                "id": "evo-x2-2-llamacpp-chat",
                "cluster_id": "evo-x2-2",
                "required": True,
                "repository": "Qwen/Qwen3-0.6B-GGUF",
                "engine": "llamacpp",
                "model_format": "gguf",
                "quantization": "q8_0",
                "runtime_profile": "product-default",
                "expected_image": None,
            }
        ],
    }
