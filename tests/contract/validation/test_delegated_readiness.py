"""Only explicit rejected submissions may be repeated; accepted jobs may not."""

from unittest.mock import Mock

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.validation import delegated_readiness as readiness

pytestmark = pytest.mark.contract
REQUEST = {
    "entrypoint": "python3 job.py",
    "target_cluster": "receiver",
    "timeout_seconds": 300,
    "delegated_access": {"datasets": [{"urn": "fixture", "operations": ["discover"]}]},
}


def _pending():
    return APIError(
        "authority pending",
        status_code=503,
        response_data={"detail": {"reason": "delegated_access_unavailable"}},
    )


def test_rejected_submission_retries_then_polls_only_the_accepted_id(monkeypatch):
    persona = Mock()
    persona.jobs.submit_async.side_effect = [_pending(), "accepted"]
    monkeypatch.setattr(readiness.time, "sleep", Mock())
    result = readiness.run_delegated_job(persona, REQUEST)
    assert result is persona.jobs.wait.return_value
    assert persona.jobs.submit_async.call_count == 2
    persona.jobs.submit_async.assert_called_with(**REQUEST)
    persona.jobs.wait.assert_called_once_with(
        "accepted", timeout=300, target_cluster="receiver"
    )


@pytest.mark.parametrize(
    "error",
    [
        APIError("denied", status_code=403),
        APIError("transport failed"),
        APIError("broken", status_code=500),
        APIError("other unavailable", status_code=503),
        APIError(
            "package catalog",
            status_code=503,
            response_data={"detail": {"reason": "job_package_catalog_unavailable"}},
        ),
        RuntimeError("connection failed"),
    ],
)
def test_other_submission_failures_do_not_replay(monkeypatch, error):
    persona = Mock()
    persona.jobs.submit_async.side_effect = error
    sleep = Mock()
    monkeypatch.setattr(readiness.time, "sleep", sleep)
    with pytest.raises(type(error)) as caught:
        readiness.run_delegated_job(persona, REQUEST)
    assert caught.value is error
    persona.jobs.submit_async.assert_called_once_with(**REQUEST)
    persona.jobs.wait.assert_not_called()
    sleep.assert_not_called()


def test_polling_failure_never_resubmits_even_with_the_same_503(monkeypatch):
    persona = Mock()
    persona.jobs.submit_async.return_value = "accepted"
    error = _pending()
    persona.jobs.wait.side_effect = error
    monkeypatch.setattr(readiness.time, "sleep", Mock())
    with pytest.raises(APIError) as caught:
        readiness.run_delegated_job(persona, REQUEST)
    assert caught.value is error
    persona.jobs.submit_async.assert_called_once_with(**REQUEST)
    persona.jobs.wait.assert_called_once()


def test_rejected_submission_deadline_preserves_original_failure(monkeypatch):
    persona = Mock()
    error = _pending()
    persona.jobs.submit_async.side_effect = error
    ticks = iter([0.0, 29.75, 30.0])
    sleep = Mock()
    monkeypatch.setattr(readiness.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(readiness.time, "sleep", sleep)
    with pytest.raises(APIError) as caught:
        readiness.run_delegated_job(persona, REQUEST)
    assert caught.value is error
    sleep.assert_called_once_with(0.25)
    assert persona.jobs.submit_async.call_count == 2
    persona.jobs.wait.assert_not_called()
