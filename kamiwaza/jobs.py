"""T5.9 / ENG-4680 — kamiwaza.jobs module skeleton.

Customer-facing surface for federated job submission per design §4.2.11.
Exposed on the client as ``kz.jobs`` (lazy-loaded — see
kamiwaza.client.Kamiwaza.jobs).

Skeleton scope (WS-M1):
    - kz.jobs.run(target_cluster, entrypoint, ...)  -> JobResult
    - kz.jobs.submit_async(target_cluster, entrypoint, ...) -> str (job_id)
    - kz.jobs.wait(job_id, *, timeout) -> JobResult

Out of scope for skeleton (land in WS-M2):
    - kz.jobs.cancel(job_id) — T5.35 / ENG-4712
    - kz.jobs.run(..., recoverable=True) — T5.22 / ENG-4699
    - kz.jobs.run(..., pip=[...], py_modules=[...]) — T5.38 / ENG-4715

Server-side correlate: ``kamiwaza.cluster.jobs`` (FederatedJobsService
+ /api/cluster/jobs/{run,submit,{id}/status,{id}/result} endpoints).
"""

from __future__ import annotations

import time
from typing import Any, Optional

from kamiwaza.exceptions import MeshJobTimeoutError
from kamiwaza.models import JobResult


# Polling backoff schedule for wait(). Mirrors the design §4.2.14
# pattern: 1s, 2s, 4s, capped at 5s. Total budget is the caller's
# `timeout` argument; the schedule just controls how often we hit the
# server while waiting.
_POLL_BACKOFF_INITIAL_SECONDS = 1.0
_POLL_BACKOFF_FACTOR = 2.0
_POLL_BACKOFF_CAP_SECONDS = 5.0

_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "STOPPED", "CANCELED"})


class JobsAPI:
    """Job submission for the local cluster + federated targets."""

    def __init__(self, client: Any) -> None:
        # client is a kamiwaza.client.Kamiwaza instance; typed as Any
        # to avoid the runtime-cycle cost (see federations.py).
        self._client = client

    def run(
        self,
        *,
        entrypoint: str,
        target_cluster: Optional[str] = None,
        runtime_env: Optional[dict[str, Any]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> JobResult:
        """Run a job synchronously and return the completed JobResult.

        Args:
            entrypoint: Shell command for Ray to execute, e.g.
                ``"python query.py"``.
            target_cluster: Federation name to route to. None runs on
                the local cluster.
            runtime_env: Ray runtime_env (env vars, working_dir, …).
                Limited to the existing /run shape in skeleton; T5.38
                adds pip / py_modules / working_dir convenience kwargs.
            timeout_seconds: Server-side wall-clock cap (informational
                only — server enforces; SDK does not poll).

        Returns:
            Completed ``JobResult``. ``status`` will be SUCCEEDED for
            success or FAILED with ``error`` populated. Customers branch
            on ``result.status`` instead of catching exceptions.
        """
        body = self._build_run_body(
            entrypoint=entrypoint,
            target_cluster=target_cluster,
            runtime_env=runtime_env,
            timeout_seconds=timeout_seconds,
        )
        response = self._client._request("POST", "/api/cluster/jobs/run", json=body)
        return JobResult.model_validate(response)

    def submit_async(
        self,
        *,
        entrypoint: str,
        target_cluster: Optional[str] = None,
        runtime_env: Optional[dict[str, Any]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> str:
        """Submit a job and return its job_id immediately.

        Use ``wait(job_id, timeout=...)`` to poll for completion. The
        async submit + poll pattern is the recommended shape for jobs
        that may exceed 60s (per design §4.2.14). The full recoverable
        helper that combines submit + wait + reconnect-on-drop lands in
        WS-M2 (T5.22 / ENG-4699).
        """
        body = self._build_run_body(
            entrypoint=entrypoint,
            target_cluster=target_cluster,
            runtime_env=runtime_env,
            timeout_seconds=timeout_seconds,
        )
        response = self._client._request("POST", "/api/cluster/jobs/submit", json=body)
        return str(response["job_id"])

    def wait(self, job_id: str, *, timeout: int) -> JobResult:
        """Poll a previously-submitted job until terminal, then return.

        Args:
            job_id: Returned by ``submit_async``.
            timeout: Wall-clock budget in seconds. On expiry, raises
                ``MeshJobTimeoutError`` so customer code can branch on
                "still running" vs "ran but failed" (which returns a
                FAILED ``JobResult``, not an exception).

        Returns:
            JobResult with status in {SUCCEEDED, FAILED, STOPPED, CANCELED}.

        Raises:
            MeshJobTimeoutError: ``timeout`` expired before the job
                reached a terminal state.
        """
        deadline = time.monotonic() + timeout
        delay = _POLL_BACKOFF_INITIAL_SECONDS
        while time.monotonic() < deadline:
            status_body = self._client._request(
                "GET", f"/api/cluster/jobs/{job_id}/status"
            )
            status = (
                status_body.get("status") if isinstance(status_body, dict) else None
            )
            if status in _TERMINAL_STATES:
                result_body = self._client._request(
                    "GET", f"/api/cluster/jobs/{job_id}/result"
                )
                return JobResult.model_validate(result_body)

            time.sleep(delay)
            delay = min(delay * _POLL_BACKOFF_FACTOR, _POLL_BACKOFF_CAP_SECONDS)

        raise MeshJobTimeoutError(
            f"Job {job_id} did not reach a terminal state within {timeout} seconds.",
            status_code=None,
            body={"job_id": job_id, "timeout_seconds": timeout},
        )

    @staticmethod
    def _build_run_body(
        *,
        entrypoint: str,
        target_cluster: Optional[str],
        runtime_env: Optional[dict[str, Any]],
        timeout_seconds: Optional[int],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"entrypoint": entrypoint}
        if target_cluster is not None:
            body["target_cluster"] = target_cluster
        if runtime_env is not None:
            body["runtime_env"] = runtime_env
        if timeout_seconds is not None:
            body["timeout_seconds"] = timeout_seconds
        return body
