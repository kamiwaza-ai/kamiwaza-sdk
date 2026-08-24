"""T7.6 / ENG-5040 — Federated job submission on the canonical surface.

WS-M3.2 service migration. Brings the customer-facing federation jobs
surface from ``kamiwaza/jobs.py`` (M1+ skeleton) into the canonical
``kamiwaza_sdk.services`` namespace per design v0.3.7 §4.2.11.

Module name: ``jobs_federation.py`` (not ``jobs.py``) per design §6.2 T7.6
to leave room for a future legacy-style local-cluster jobs service should
one be needed. The class name remains ``JobsAPI`` for consistency with the
M1+ API surface customers already learned.

Skeleton scope (WS-M1):
    - kz.jobs.run(target_cluster, entrypoint, ...)  -> JobResult
    - kz.jobs.submit_async(target_cluster, entrypoint, ...) -> str (job_id)
    - kz.jobs.wait(job_id, *, timeout) -> JobResult

Operability scope (WS-M2):
    - kz.jobs.cancel(job_id) — T5.35 / ENG-4712
    - kz.jobs.run(..., recoverable=True) — T5.22 / ENG-4699
    - kz.jobs.run(..., pip=[...], py_modules=[...]) — ordinary Ray runtime env
    - kz.jobs.run(..., python_packages=[...]) — approved delegated-job packages

Server-side correlate: ``kamiwaza.cluster.jobs`` (FederatedJobsService
+ /api/cluster/jobs/{run,submit,{id}/status,{id}/result} endpoints).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Optional

from ..exceptions import APIError, MeshJobTimeoutError
from ..schemas.federation import JobResult
from ..schemas.delegated_jobs import DelegatedAccess, normalize_python_packages
from .base_service import BaseService
from .jobs_routing import JobRouter

# Polling backoff schedule for wait(). Mirrors the design §4.2.14
# pattern: 1s, 2s, 4s, capped at 5s. Total budget is the caller's
# `timeout` argument; the schedule just controls how often we hit the
# server while waiting.
_POLL_BACKOFF_INITIAL_SECONDS = 1.0
_POLL_BACKOFF_FACTOR = 2.0
_POLL_BACKOFF_CAP_SECONDS = 5.0

_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "STOPPED", "CANCELED"})
_UNSUCCESSFUL_TERMINAL_STATES = _TERMINAL_STATES - {"SUCCEEDED"}

# A JobResult-shaped /result wrapper is identified by these two required-field
# keys both present; a body without them is a bare marker (the job's own
# KZ_MESH_RUN_ON_JSON:: output). See ``_marker_to_payload``.
_JOBRESULT_WRAPPER_KEYS = ("job_id", "status")

# The single field promoted out of a bare marker: the receiver-injected OBO
# identity the audit-actor demo surfaces as ``JobResult.audit_actor``. Every
# other bare-marker key stays opaque under ``result`` (never promoted or
# validated against a JobResult field type).
_BARE_MARKER_PROMOTED_FIELD = "audit_actor"


class JobsAPI(BaseService):
    """Job submission for the local cluster + federated targets."""

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._router = JobRouter(client)

    def run(
        self,
        *,
        entrypoint: str,
        target_cluster: Optional[str] = None,
        runtime_env: Optional[dict[str, Any]] = None,
        timeout_seconds: Optional[int] = None,
        recoverable: bool = False,
        pip: Optional[list[str]] = None,
        py_modules: Optional[list[str]] = None,
        working_dir: Optional[str] = None,
        delegated_access: DelegatedAccess | Mapping[str, Any] | None = None,
        python_packages: Optional[list[str]] = None,
    ) -> JobResult:
        """Run a job and return the completed JobResult.

        Args:
            entrypoint: Shell command for Ray to execute, e.g.
                ``"python query.py"``.
            target_cluster: Federation name to route to. None runs on
                the local cluster.
            runtime_env: Ray runtime_env (env vars, working_dir, …).
                Caller-provided keys win over the convenience kwargs
                below on collision.
            timeout_seconds: Wall-clock cap. Server-enforced for
                ``recoverable=False``; SDK-enforced poll budget for
                ``recoverable=True``.
            recoverable: When True (T5.22 / ENG-4699), the SDK uses async
                submit + poll instead of the sync /run path so the
                ``job_id`` is in hand immediately.
            pip: T5.38 / ENG-4715 / FR-94 convenience — Ray pip list,
                packed into ``runtime_env["pip"]`` on the wire.
                For isolated delegated jobs, use ``python_packages`` instead;
                Core strips execution-environment runtime keys at that boundary.
            py_modules: T5.38 / ENG-4715 / FR-94 convenience — local
                module paths to ship with the job; packs into
                ``runtime_env["py_modules"]``.
            working_dir: T5.38 / ENG-4715 / FR-94 convenience — local
                directory to bundle as the working dir; packs into
                ``runtime_env["working_dir"]``.
            delegated_access: Exact receiver datasets/models and their typed
                operations. Omission preserves the ordinary job path.
            python_packages: Exact ``name==version`` coordinates from the
                receiver operator's approved private package catalog. This is
                available only with ``delegated_access``; repository location
                and credentials are never part of the request.

        Returns:
            Completed ``JobResult``. ``status`` will be SUCCEEDED for
            success or FAILED with ``error`` populated. Customers branch
            on ``result.status`` instead of catching exceptions.

        Raises:
            MeshJobTimeoutError: Only on the recoverable path, when
                ``timeout_seconds`` expires before the job reaches a
                terminal state.
        """
        merged_runtime_env = self._merge_runtime_env(
            runtime_env=runtime_env,
            pip=pip,
            py_modules=py_modules,
            working_dir=working_dir,
        )
        if recoverable:
            return self._run_recoverable(
                entrypoint=entrypoint,
                target_cluster=target_cluster,
                runtime_env=merged_runtime_env,
                timeout_seconds=timeout_seconds,
                delegated_access=delegated_access,
                python_packages=python_packages,
            )
        return self._run_sync(
            entrypoint=entrypoint,
            target_cluster=target_cluster,
            runtime_env=merged_runtime_env,
            timeout_seconds=timeout_seconds,
            delegated_access=delegated_access,
            python_packages=python_packages,
        )

    @staticmethod
    def _merge_runtime_env(
        *,
        runtime_env: Optional[dict[str, Any]],
        pip: Optional[list[str]],
        py_modules: Optional[list[str]],
        working_dir: Optional[str],
    ) -> Optional[dict[str, Any]]:
        """Pack convenience kwargs (T5.38) into a runtime_env dict.

        Caller-supplied runtime_env wins on key collision — the
        convenience kwargs are sugar, not overrides. Returns None when
        no source provided so the body shape matches the pre-T5.38
        default-caller pattern (no runtime_env key on the wire).
        """
        convenience: dict[str, Any] = {}
        if pip is not None:
            convenience["pip"] = pip
        if py_modules is not None:
            convenience["py_modules"] = py_modules
        if working_dir is not None:
            convenience["working_dir"] = working_dir

        if not convenience and runtime_env is None:
            return None

        merged: dict[str, Any] = dict(convenience)
        if runtime_env:
            merged.update(runtime_env)  # caller wins on collision
        return merged

    def _run_sync(
        self,
        *,
        entrypoint: str,
        target_cluster: Optional[str],
        runtime_env: Optional[dict[str, Any]],
        timeout_seconds: Optional[int],
        delegated_access: DelegatedAccess | Mapping[str, Any] | None,
        python_packages: Optional[list[str]],
    ) -> JobResult:
        """Existing sync /run path; X-Job-Id only visible on completion."""
        body = _build_run_body(
            entrypoint=entrypoint,
            runtime_env=runtime_env,
            timeout_seconds=timeout_seconds,
            delegated_access=delegated_access,
            python_packages=python_packages,
        )
        response = self._router.request(
            "POST", "run", target_cluster=target_cluster, json=body
        )
        return JobResult.model_validate(response)

    def _run_recoverable(
        self,
        *,
        entrypoint: str,
        target_cluster: Optional[str],
        runtime_env: Optional[dict[str, Any]],
        timeout_seconds: Optional[int],
        delegated_access: DelegatedAccess | Mapping[str, Any] | None,
        python_packages: Optional[list[str]],
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
            delegated_access=delegated_access,
            python_packages=python_packages,
        )
        return self.wait(
            job_id,
            timeout=timeout_seconds or 600,
            target_cluster=target_cluster,
        )

    def submit_async(
        self,
        *,
        entrypoint: str,
        target_cluster: Optional[str] = None,
        runtime_env: Optional[dict[str, Any]] = None,
        timeout_seconds: Optional[int] = None,
        delegated_access: DelegatedAccess | Mapping[str, Any] | None = None,
        python_packages: Optional[list[str]] = None,
    ) -> str:
        """Submit a job and return its job_id immediately.

        Use ``wait(job_id, timeout=...)`` to poll for completion. The
        async submit + poll pattern is the recommended shape for jobs
        that may exceed 60s (per design §4.2.14).
        """
        body = _build_run_body(
            entrypoint=entrypoint,
            runtime_env=runtime_env,
            timeout_seconds=timeout_seconds,
            delegated_access=delegated_access,
            python_packages=python_packages,
        )
        response = self._router.request(
            "POST", "submit", target_cluster=target_cluster, json=body
        )
        job_id = str(response["job_id"])
        self._router.remember(job_id, target_cluster)
        return job_id

    def cancel(
        self, job_id: str, *, target_cluster: Optional[str] = None
    ) -> dict[str, Any]:
        """Cancel a running job (T5.35 / ENG-4712).

        POSTs to ``/api/cluster/jobs/{id}/cancel``. The server returns a
        JobRecord; we surface the raw dict so customers can inspect
        ``status`` (typically STOPPED on success) and timestamps.

        Demo bullet (3): ``kz.jobs.cancel(job_id)`` stops a stuck job
        within seconds.
        """
        target = self._router.resolve(job_id, target_cluster)
        response = self._router.request(
            "POST", f"{job_id}/cancel", target_cluster=target
        )
        return dict(response)

    def wait(
        self,
        job_id: str,
        *,
        timeout: int,
        target_cluster: Optional[str] = None,
    ) -> JobResult:
        """Poll a previously-submitted job until terminal, then return.

        Args:
            job_id: Returned by ``submit_async``.
            timeout: Wall-clock budget in seconds. On expiry, raises
                ``MeshJobTimeoutError`` so customer code can branch on
                "still running" vs "ran but failed" (which returns a
                FAILED ``JobResult``, not an exception).
            target_cluster: Federation used for the original submission.
                Required when resuming a remotely submitted job.

        Returns:
            JobResult with status in {SUCCEEDED, FAILED, STOPPED, CANCELED}.

        Raises:
            MeshJobTimeoutError: ``timeout`` expired before the job
                reached a terminal state.
        """
        deadline = time.monotonic() + timeout
        delay = _POLL_BACKOFF_INITIAL_SECONDS
        target = self._router.resolve(job_id, target_cluster)
        while time.monotonic() < deadline:
            status_body = self._router.request(
                "GET", f"{job_id}/status", target_cluster=target
            )
            status = (
                status_body.get("status") if isinstance(status_body, dict) else None
            )
            if status in _TERMINAL_STATES:
                return self._terminal_result(job_id, status, target)

            time.sleep(delay)
            delay = min(delay * _POLL_BACKOFF_FACTOR, _POLL_BACKOFF_CAP_SECONDS)

        raise MeshJobTimeoutError(
            f"Job {job_id} did not reach a terminal state within {timeout} seconds.",
            status_code=None,
            body={"job_id": job_id, "timeout_seconds": timeout},
        )

    def _terminal_result(
        self, job_id: str, status: str, target_cluster: Optional[str]
    ) -> JobResult:
        """Fetch a terminal marker when Core makes one available."""
        payload: dict[str, Any] = {}
        try:
            result_body = self._router.request(
                "GET", f"{job_id}/result", target_cluster=target_cluster
            )
            payload = self._marker_to_payload(result_body)
        except APIError as exc:
            if not _result_error_is_ignorable(status, exc.status_code):
                raise
        return JobResult.model_validate(
            {**payload, "job_id": str(job_id), "status": status}
        )

    @staticmethod
    def _marker_to_payload(result_body: Any) -> dict[str, Any]:
        """Map a /result body into JobResult constructor fields.

        /result returns one of two shapes, handled by two total rules:

        - **JobResult wrapper** — the server's own JobResult dict, identified by
          ``job_id`` AND ``status`` both present. Passes through wholesale, so
          declared fields surface and undeclared keys stay as forward-compat
          ``extra="allow"`` extras at top level.
        - **Bare marker** — the job's own KZ_MESH_RUN_ON_JSON:: output, which is
          opaque domain data. It nests under ``result`` untouched, so a key that
          happens to be named like a JobResult field (``error``/``status``/
          ``result``) is never promoted, validated against that field's type
          (which could raise), or dropped. The one promoted field is
          ``audit_actor`` (the OBO identity the audit demo surfaces) — and only
          when it is a string, never a colliding non-string value.

        A non-dict body wraps as ``{"result": <body>}``.
        """
        if not isinstance(result_body, dict):
            return {"result": result_body}
        if all(k in result_body for k in _JOBRESULT_WRAPPER_KEYS):
            return dict(result_body)
        domain = dict(result_body)
        payload: dict[str, Any] = {}
        actor = domain.get(_BARE_MARKER_PROMOTED_FIELD)
        if isinstance(actor, str):
            payload[_BARE_MARKER_PROMOTED_FIELD] = domain.pop(
                _BARE_MARKER_PROMOTED_FIELD
            )
        if domain:
            payload["result"] = domain
        return payload


def _result_error_is_ignorable(status: str, status_code: int | None) -> bool:
    if status_code == 410:
        return True
    if status not in _UNSUCCESSFUL_TERMINAL_STATES:
        return False
    return status_code == 409


def _build_run_body(
    *,
    entrypoint: str,
    runtime_env: Optional[dict[str, Any]],
    timeout_seconds: Optional[int],
    delegated_access: DelegatedAccess | Mapping[str, Any] | None,
    python_packages: Optional[list[str]],
) -> dict[str, Any]:
    body: dict[str, Any] = {"entrypoint": entrypoint}
    if runtime_env is not None:
        body["runtime_env"] = runtime_env
    if timeout_seconds is not None:
        body["timeout_seconds"] = timeout_seconds
    if delegated_access is not None:
        access = DelegatedAccess.model_validate(delegated_access)
        body["delegated_access"] = access.model_dump(mode="json")
    packages = normalize_python_packages(python_packages)
    if packages and delegated_access is None:
        raise ValueError("python_packages require delegated_access")
    if packages:
        body["python_packages"] = list(packages)
    return body
