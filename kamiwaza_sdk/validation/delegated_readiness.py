"""Retry explicit pre-admission rejections without replaying accepted jobs."""

from __future__ import annotations

import logging
import time
from typing import Any

from kamiwaza_sdk.exceptions import APIError

logger = logging.getLogger(__name__)
_ADMISSION_TIMEOUT_SECONDS = 30.0


def run_delegated_job(persona: Any, request: dict[str, Any]) -> Any:
    """Submit through a bounded authority delay, then poll the accepted ID once.

    Core's delegated authorizer emits this exact rejection before persisting
    or dispatching a job. Other errors, including ambiguous transport failures,
    propagate. Polling stays outside the submission retry boundary.
    """
    job_id = _submit_when_authorized(persona, request)
    return persona.jobs.wait(
        job_id,
        timeout=request["timeout_seconds"],
        target_cluster=request["target_cluster"],
    )


def _submit_when_authorized(persona: Any, request: dict[str, Any]) -> str:
    deadline = time.monotonic() + _ADMISSION_TIMEOUT_SECONDS
    while True:
        try:
            return str(persona.jobs.submit_async(**request))
        except APIError as exc:
            if not _admission_pending(exc):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            logger.info("delegated_job_authority_pending_before_admission")
            time.sleep(min(1.0, remaining))


def _admission_pending(error: APIError) -> bool:
    if error.status_code != 503:
        return False
    return error.response_data == {"detail": {"reason": "delegated_access_unavailable"}}
