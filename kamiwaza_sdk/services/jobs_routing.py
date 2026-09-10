"""Routing state and mesh request construction for cluster jobs."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

from .federation_credentials import federation_credential_headers

_MAX_TRACKED_REMOTE_JOBS = 256


class JobRouter:
    """Route local and federated job requests and remember recent targets."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._remote_job_targets: dict[str, str] = {}

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        target_cluster: Optional[str],
        **kwargs: Any,
    ) -> Any:
        local_path = f"/cluster/jobs/{endpoint}"
        if target_cluster is None:
            return self._client._request(method, local_path, **kwargs)

        selector = _validate_target_cluster(target_cluster)
        headers = federation_credential_headers(selector)
        if headers:
            kwargs["headers"] = headers
        mesh_path = f"/mesh/{quote(selector, safe='')}/api{local_path}"
        return self._client._request(method, mesh_path, **kwargs)

    def remember(self, job_id: str, target_cluster: Optional[str]) -> None:
        """Remember bounded same-client routing state for async job handles."""
        if target_cluster is None:
            return
        self._remote_job_targets[job_id] = target_cluster
        if len(self._remote_job_targets) > _MAX_TRACKED_REMOTE_JOBS:
            oldest_job_id = next(iter(self._remote_job_targets))
            del self._remote_job_targets[oldest_job_id]

    def resolve(self, job_id: str, target_cluster: Optional[str]) -> Optional[str]:
        """Prefer an explicit target, falling back to this client's submit cache."""
        if target_cluster is not None:
            return target_cluster
        return self._remote_job_targets.get(job_id)


def _validate_target_cluster(target_cluster: str) -> str:
    """Require a trimmed mesh selector that fits one URL path segment."""
    stripped = target_cluster.strip()
    if not stripped:
        raise _invalid_target_cluster()
    if stripped != target_cluster:
        raise _invalid_target_cluster()
    if _is_unsafe_path_segment(target_cluster):
        raise _invalid_target_cluster()
    return target_cluster


def _is_unsafe_path_segment(target_cluster: str) -> bool:
    return "/" in target_cluster or target_cluster in {".", ".."}


def _invalid_target_cluster() -> ValueError:
    return ValueError(
        "target_cluster must be a trimmed, nonblank federation name or ID "
        "that fits one URL path segment"
    )
