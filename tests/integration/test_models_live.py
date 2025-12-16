from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]

CANONICAL_REPO = "mlx-community/Qwen3-4B-4bit"


def test_live_model_metadata_and_download(live_kamiwaza_client, ensure_repo_ready) -> None:
    target = ensure_repo_ready(live_kamiwaza_client, CANONICAL_REPO)

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
