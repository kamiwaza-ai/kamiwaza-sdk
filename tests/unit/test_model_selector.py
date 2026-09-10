from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kamiwaza_sdk.model_selector import ModelAutoSelector

pytestmark = pytest.mark.unit


def _selector() -> ModelAutoSelector:
    return object.__new__(ModelAutoSelector)


def test_downloaded_index_requires_storage_without_pending_download():
    model = SimpleNamespace(
        repo_modelId="org/ready",
        m_files=[
            SimpleNamespace(
                storage_location="oci://models/org-ready/weights",
                is_downloading=False,
                dl_requested_at=None,
                download=True,
            )
        ],
    )

    assert _selector()._build_downloaded_index([model]) == {"org/ready": True}


def test_downloaded_index_ignores_download_intent_without_ready_storage():
    model = SimpleNamespace(
        repo_modelId="org/pending",
        m_files=[
            SimpleNamespace(
                storage_location="oci://models/org-pending/weights",
                is_downloading=False,
                dl_requested_at=datetime.now(timezone.utc),
                download=True,
            )
        ],
    )

    assert _selector()._build_downloaded_index([model]) == {}

