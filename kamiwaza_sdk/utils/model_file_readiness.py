"""Shared readiness predicates for model files."""

from __future__ import annotations

from typing import Any


def model_file_download_satisfied(file: Any) -> bool:
    """Return true when a model file is downloaded and not queued again.

    ``download`` is request/selection intent. A file is actually ready only
    after the server has storage, no active worker, and no queued redownload.
    """

    return (
        bool(getattr(file, "storage_location", None))
        and not bool(getattr(file, "is_downloading", False))
        and getattr(file, "dl_requested_at", None) is None
    )
