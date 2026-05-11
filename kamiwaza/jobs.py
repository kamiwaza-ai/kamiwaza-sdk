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
        recoverable: bool = False,
    ) -> JobResult:
        """Run a job and return the completed JobResult.

        Args:
            entrypoint: Shell command for Ray to execute, e.g.
                ``"python query.py"``.
            target_cluster: Federation name to route to. None runs on
                the local cluster.
            runtime_env: Ray runtime_env (env vars, working_dir, …).
            timeout_seconds: Wall-clock cap. Server-enforced for
                ``recoverable=False``; SDK-enforced poll budget for
                ``recoverable=True``.
            recoverable: When True (T5.22 / ENG-4699), the SDK uses async
                submit + poll instead of the sync /run path so the
                ``job_id`` is in hand immediately. A connection drop
                mid-job is recoverable via ``kz.jobs.wait(job_id, ...)``.
                Recommended for any job with ``timeout_seconds > 60``;
                the sync path holds the HTTP connection for the full
                duration and FastAPI buffers the X-Job-Id header until
                completion — a mid-job drop loses the job_id (see
                design §4.2.14 + SDK README "Recoverable long-jobs").

        Returns:
            Completed ``JobResult``. ``status`` will be SUCCEEDED for
            success or FAILED with ``error`` populated. Customers branch
            on ``result.status`` instead of catching exceptions.

        Raises:
            MeshJobTimeoutError: Only on the recoverable path, when
                ``timeout_seconds`` expires before the job reaches a
                terminal state.
        """
        if recoverable:
            return self._run_recoverable(
                entrypoint=entrypoint,
                target_cluster=target_cluster,
                runtime_env=runtime_env,
                timeout_seconds=timeout_seconds,
            )
        return self._run_sync(
            entrypoint=entrypoint,
            target_cluster=target_cluster,
            runtime_env=runtime_env,
            timeout_seconds=timeout_seconds,
        )

    def _run_sync(
        self,
        *,
        entrypoint: str,
        target_cluster: Optional[str],
        runtime_env: Optional[dict[str, Any]],
        timeout_seconds: Optional[int],
    ) -> JobResult:
        """Existing sync /run path; X-Job-Id only visible on completion."""
        body = self._build_run_body(
            entrypoint=entrypoint,
            target_cluster=target_cluster,
            runtime_env=runtime_env,
            timeout_seconds=timeout_seconds,
        )
        response = self._client._request("POST", "/api/cluster/jobs/run", json=body)
        return JobResult.model_validate(response)

    def _run_recoverable(
        self,
        *,
        entrypoint: str,
        target_cluster: Optional[str],
        runtime_env: Optional[dict[str, Any]],
        timeout_seconds: Optional[int],
    ) -> JobResult:
        """Async submit + poll. job_id available immediately for resume.

        Per design §4.2.14: returns when the server reports a terminal
        state, or raises MeshJobTimeoutError when ``timeout_seconds``
        expires. The wait_seconds default (600s) matches the existing
        sync /run default behavior for parity.
        """
        job_id = self.submit_async(
            entrypoint=entrypoint,
            target_cluster=target_cluster,
            runtime_env=runtime_env,
            timeout_seconds=timeout_seconds,
        )
        return self.wait(job_id, timeout=timeout_seconds or 600)

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
