"""Cold model acquisition verification against a live platform.

Set KAMIWAZA_COLD_MODEL_REPO to a fresh, uncached single-file GGUF repository
for each run. Warm files cannot prove a new download, so this test skips them.
The acquired file is retained because shared model weights may serve other users.
"""

from __future__ import annotations

import os
import time

import pytest

from kamiwaza_sdk.utils.model_file_readiness import model_file_download_satisfied

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _target_file(client, repo_id: str, quantization: str):
    matches = client.models.search_models(repo_id, exact=True, load_files=True)
    model = next((item for item in matches if item.repo_modelId == repo_id), None)
    assert model is not None, f"Model search did not return exact repo {repo_id}"
    files = [
        file
        for file in model.m_files or []
        if file.name
        and file.name.lower().endswith(".gguf")
        and client.models.quant_manager.match_quantization(file.name, quantization)
    ]
    assert len(files) == 1, (
        f"Expected one {quantization} GGUF file for {repo_id}; "
        f"found {[file.name for file in files]}"
    )
    return files[0]


def _stored_file(client, repo_id: str, filename: str):
    model = client.models.get_model_by_repo_id(repo_id)
    if not model or not model.id:
        return None
    return next(
        (
            file
            for file in client.models.get_model_files_by_model_id(model.id)
            if file.name == filename
        ),
        None,
    )


def _is_stored(file) -> bool:
    return file is not None and model_file_download_satisfied(file)


def _is_pending(file) -> bool:
    return file is not None and bool(file.is_downloading or file.dl_requested_at)


def _require_cold_target(repo_id: str, hub_file, stored_file) -> None:
    if _is_stored(hub_file) or _is_stored(stored_file):
        pytest.skip(
            f"{repo_id} {hub_file.name} is already stored; no cold run possible"
        )
    if _is_pending(hub_file) or _is_pending(stored_file):
        pytest.skip(f"{repo_id} {hub_file.name} already has a download in progress")


def _assert_acquired_file(client, file) -> None:
    assert file.id is not None
    assert file.size and file.size > 0
    assert file.storage_location
    fetched = client.models.get_model_file(file.id)
    assert model_file_download_satisfied(fetched)
    assert fetched.size == file.size


def test_cold_model_search_download_and_acquired_file(live_kamiwaza_client) -> None:
    """Prove a chosen quantized weight moves from absent to stored, not warm reuse."""
    repo_id = os.getenv("KAMIWAZA_COLD_MODEL_REPO", "Qwen/Qwen3-0.6B-GGUF")
    quantization = os.getenv("KAMIWAZA_COLD_MODEL_QUANTIZATION", "q8_0")
    timeout = int(os.getenv("KAMIWAZA_COLD_MODEL_TIMEOUT_SECONDS", "900"))
    client = live_kamiwaza_client

    before = _target_file(client, repo_id, quantization)
    _require_cold_target(repo_id, before, _stored_file(client, repo_id, before.name))

    started = client.models.initiate_model_download(repo_id, quantization=quantization)
    request = started["download_request"]
    assert request is not None, "SDK reused a warm file instead of starting download"
    assert before.name in request.files_to_download
    assert started["result"].get("result") is True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        after = _stored_file(client, repo_id, before.name)
        if after and model_file_download_satisfied(after):
            _assert_acquired_file(client, after)
            return
        time.sleep(5)

    pytest.fail(
        f"Cold download did not yield an acquired {quantization} file for "
        f"{repo_id} within {timeout}s"
    )
