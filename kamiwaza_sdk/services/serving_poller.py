"""Polling helpers for asynchronous serving deployment operations."""

from __future__ import annotations

import time
from typing import Callable, Iterable, Optional, TYPE_CHECKING, Union
from uuid import UUID

from ..exceptions import APIError, DeploymentFailedError
from ..schemas.serving.serving import ModelDeployment

if TYPE_CHECKING:
    from .serving import ServingService


# Status reads are used by client-side deployment readiness and cleanup
# pollers. Keep each transport request bounded so a stalled ingress cannot
# outlive the caller's documented polling budget indefinitely.
DEPLOYMENT_STATUS_REQUEST_TIMEOUT_SECONDS = 30.0


def _is_transient_poll_error(exc: APIError) -> bool:
    """Return whether a poll failure is safe to retry."""
    status_code = getattr(exc, "status_code", None)
    return status_code is None or status_code >= 500


class DeploymentStatusPoller:
    """Poll deployment status until it reaches a desired or failure state."""

    #: Consecutive transient poll failures tolerated before propagating.
    MAX_TRANSIENT_POLL_ERRORS = 3

    def __init__(
        self,
        service: "ServingService",
        *,
        poll_interval: float = 5.0,
        timeout: Optional[float] = 600.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._sleep = sleep_fn
        self._time = time_fn

    def wait_for(
        self,
        deployment_id: Union[str, UUID],
        *,
        desired_status: Iterable[str],
        failure_status: Iterable[str],
    ) -> ModelDeployment:
        deployment_uuid = UUID(str(deployment_id))
        desired = {status.upper() for status in desired_status}
        failures = {status.upper() for status in failure_status}
        start = self._time()
        transient_errors = 0
        while True:
            deployment, transient_errors = self._poll_deployment(
                deployment_uuid,
                desired,
                failures,
                transient_errors,
                self._request_timeout(start, deployment_uuid, desired),
            )
            if deployment is not None:
                return deployment
            self._raise_if_expired(start, deployment_uuid, desired)
            self._sleep_for_next_poll(start)

    def _poll_deployment(
        self,
        deployment_uuid: UUID,
        desired: set[str],
        failures: set[str],
        transient_errors: int,
        request_timeout: Optional[float],
    ) -> tuple[Optional[ModelDeployment], int]:
        try:
            deployment = self._service.get_deployment(
                deployment_uuid,
                timeout_seconds=request_timeout,
            )
        except APIError as exc:
            transient_errors += 1
            if not _is_transient_poll_error(exc):
                raise
            if transient_errors >= self.MAX_TRANSIENT_POLL_ERRORS:
                raise
            return None, transient_errors

        current = (deployment.status or "").upper()
        if current in desired:
            return deployment, 0
        if failures and current in failures:
            self._raise_failure(deployment, deployment_uuid)
        return None, 0

    def _request_timeout(
        self, start: float, deployment_uuid: UUID, desired: set[str]
    ) -> Optional[float]:
        """Return a transport timeout bounded by the caller's deadline."""
        remaining = self._remaining_budget(start)
        if remaining is not None and remaining <= 0:
            self._raise_timeout(deployment_uuid, desired)
        if remaining is None:
            return None
        return min(DEPLOYMENT_STATUS_REQUEST_TIMEOUT_SECONDS, remaining)

    def _remaining_budget(self, start: float) -> Optional[float]:
        if self._timeout is None:
            return None
        return self._timeout - (self._time() - start)

    def _raise_if_expired(
        self, start: float, deployment_uuid: UUID, desired: set[str]
    ) -> None:
        remaining = self._remaining_budget(start)
        if remaining is not None and remaining <= 0:
            self._raise_timeout(deployment_uuid, desired)

    def _sleep_for_next_poll(self, start: float) -> None:
        """Sleep for the poll interval without exceeding the caller budget."""
        if self._poll_interval <= 0:
            return
        if self._timeout is None:
            self._sleep(self._poll_interval)
            return
        remaining = self._timeout - (self._time() - start)
        if remaining > 0:
            self._sleep(min(self._poll_interval, remaining))

    @staticmethod
    def _raise_timeout(deployment_uuid: UUID, desired: set[str]) -> None:
        timeout_error = TimeoutError(
            f"Timed out waiting for deployment {deployment_uuid} to reach {desired}"
        )
        setattr(timeout_error, "deployment_id", str(deployment_uuid))
        raise timeout_error

    @staticmethod
    def _raise_failure(deployment: ModelDeployment, deployment_uuid: UUID) -> None:
        last_error_message = getattr(deployment, "last_error_message", None)
        last_error_code = getattr(deployment, "last_error_code", None)
        message = (
            f"Deployment {deployment_uuid} entered failure status {deployment.status}"
        )
        if last_error_message:
            message = f"{message}: {last_error_message}"
        raise DeploymentFailedError(
            message,
            status=deployment.status,
            last_error_message=last_error_message,
            last_error_code=last_error_code,
            deployment_id=str(deployment_uuid),
        )
