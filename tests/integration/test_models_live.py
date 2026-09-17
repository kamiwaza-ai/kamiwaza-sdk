from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def test_live_model_metadata_and_download(
    live_kamiwaza_client, ensure_model_lifecycle_target_ready
) -> None:
    target = ensure_model_lifecycle_target_ready(live_kamiwaza_client)

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
