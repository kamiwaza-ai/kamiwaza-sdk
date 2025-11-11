from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]

CANONICAL_REPO = "mlx-community/Qwen3-4B-4bit"


def test_live_model_metadata_and_download(live_kamiwaza_client) -> None:
    models = live_kamiwaza_client.models.list_models(load_files=False)
    if not models:
        pytest.skip("No models registered on live server")

    target = next((m for m in models if getattr(m, "repo_modelId", None) == CANONICAL_REPO), None)
    if not target:
        pytest.skip(f"{CANONICAL_REPO} is not registered on the live server")

    # Ensure get_model works round-trip
    detailed = live_kamiwaza_client.models.get_model(str(target.id))
    assert detailed.name

    payload = {
        "model": target.repo_modelId,
        "hub": getattr(target, "hub", None) or "hf",
        "files_to_download": ["README.md"],
    }
    try:
        response = live_kamiwaza_client.post("/models/download/", json=payload)
    except APIError as exc:
        pytest.skip(f"Model download API unavailable: {exc}")
    else:
        assert isinstance(response, dict)
        assert response.get("result", True) in (True, None)
