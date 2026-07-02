from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kamiwaza_sdk.utils.model_file_readiness import model_file_download_satisfied

pytestmark = pytest.mark.unit


def test_model_file_download_satisfied_requires_storage_and_no_pending_work():
    ready_file = SimpleNamespace(
        storage_location="oci://models/org-model/q4_k_m",
        is_downloading=False,
        dl_requested_at=None,
        download=True,
    )

    assert model_file_download_satisfied(ready_file) is True


@pytest.mark.parametrize(
    "file",
    [
        SimpleNamespace(
            storage_location=None,
            is_downloading=False,
            dl_requested_at=None,
            download=True,
        ),
        SimpleNamespace(
            storage_location="oci://models/org-model/q4_k_m",
            is_downloading=True,
            dl_requested_at=None,
            download=True,
        ),
        SimpleNamespace(
            storage_location="oci://models/org-model/q4_k_m",
            is_downloading=False,
            dl_requested_at=datetime.now(timezone.utc),
            download=True,
        ),
    ],
)
def test_model_file_download_satisfied_rejects_incomplete_states(file):
    assert model_file_download_satisfied(file) is False
