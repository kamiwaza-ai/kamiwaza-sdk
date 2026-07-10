from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from kamiwaza_sdk.schemas.models.downloads import ModelDownloadRequest, ModelDownloadStatus
from kamiwaza_sdk.schemas.models.model_file import ModelFile, StorageType

pytestmark = pytest.mark.unit


def test_model_download_request_dump():
    req = ModelDownloadRequest(
        model="mlx-community/Qwen3-4B-4bit",
        version="main",
        hub="hf",
        files_to_download=["README.md"],
    )
    data = req.model_dump()
    assert data["files_to_download"] == ["README.md"]


def test_model_download_status_string_includes_progress():
    status = ModelDownloadStatus(
        id=uuid4(),
        m_id=uuid4(),
        name="Qwen snapshot",
        is_downloading=True,
        download_percentage=42,
        download_throughput="12 MB/s",
        download_elapsed="00:10",
        dl_requested_at=datetime.now(timezone.utc),
    )
    text = str(status)
    assert "42%" in text
    assert "12 MB/s" in text


@pytest.mark.parametrize(
    "value",
    ["file", "s3", "gcs", "azureblob", "scratch", "oci"],
)
def test_storage_type_accepts_all_server_values(value):
    """ModelFile.storage_type must accept every value the server emits.

    Server enum (kamiwaza/services/models/schemas/model_file.py) lists
    FILE, S3, GCS, AZUREBLOB, SCRATCH, OCI. A missing value here causes
    a ValidationError when list_models() returns rows with that storage
    type — see the cascade root-caused during Phase 3 smoke.
    """
    mf = ModelFile.model_validate({"name": "weights.bin", "storage_type": value})
    assert mf.storage_type == StorageType(value)
