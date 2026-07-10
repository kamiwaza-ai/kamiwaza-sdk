"""T5.9 / ENG-4680 — JobsAPI skeleton tests on canonical surface.

WS-M3.2 test migration (T7.15 / ENG-5049). Skeleton scope per design §6.2 WS-M1 T5.9:
    - kz.jobs.run(target_cluster, entrypoint, ...) -> JobResult
    - kz.jobs.submit_async(...) -> str (job_id)
    - kz.jobs.wait(job_id, timeout=...) -> JobResult
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_run_synchronous_returns_job_result(mock_client) -> None:
    """``jobs.run(target_cluster, entrypoint, ...)`` hits the synchronous
    ``/cluster/jobs/run`` endpoint which returns the completed JobResult
    in-line (no separate poll)."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    mock_client.expect(
        "POST",
        "/cluster/jobs/run",
        {
            "job_id": "job-abc-123",
            "status": "SUCCEEDED",
            "result": {"answer": "42"},
            "audit_actor": "cdr-baker@LYRA",
        },
    )

    result = JobsAPI(client=mock_client).run(
        target_cluster="ORION",
        entrypoint="python -c 'print(42)'",
    )

    assert result.status == "SUCCEEDED"
    assert result.audit_actor == "cdr-baker@LYRA"

    _method, _path, kwargs = mock_client.calls[0]
    body = kwargs.get("json", {})
    assert body["target_cluster"] == "ORION"
    assert "entrypoint" in body


def test_run_local_when_target_cluster_omitted(mock_client) -> None:
    """When ``target_cluster`` is not provided, the job runs on the local
    cluster. Wire shape doesn't include the ``target_cluster`` key."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    mock_client.expect(
        "POST",
        "/cluster/jobs/run",
        {"job_id": "job-local", "status": "SUCCEEDED", "result": {"local": True}},
    )

    result = JobsAPI(client=mock_client).run(entrypoint="python -c 'pass'")
    assert result.status == "SUCCEEDED"

    _method, _path, kwargs = mock_client.calls[0]
    body = kwargs.get("json", {})
    assert "target_cluster" not in body


def test_submit_async_returns_job_id(mock_client) -> None:
    """submit_async POSTs to ``/cluster/jobs/submit`` and returns just
    the job_id immediately. Customer polls via wait()."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    mock_client.expect("POST", "/cluster/jobs/submit", {"job_id": "job-async-456"})

    job_id = JobsAPI(client=mock_client).submit_async(
        target_cluster="ORION",
        entrypoint="python big_job.py",
    )

    assert job_id == "job-async-456"


def test_wait_polls_until_terminal_state(mock_client) -> None:
    """wait() polls ``/cluster/jobs/{id}/status`` with backoff until the
    status is terminal, then fetches the result and returns JobResult."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    job_id = "job-wait-789"

    mock_client.expect_sequence(
        "GET",
        f"/cluster/jobs/{job_id}/status",
        [
            {"status": "PENDING"},
            {"status": "RUNNING"},
            {"status": "SUCCEEDED"},
        ],
    )
    mock_client.expect(
        "GET",
        f"/cluster/jobs/{job_id}/result",
        {"job_id": job_id, "status": "SUCCEEDED", "result": {"value": "done"}},
    )

    with patch("time.sleep") as sleep_mock:
        result = JobsAPI(client=mock_client).wait(job_id, timeout=60)

    assert result.status == "SUCCEEDED"
    assert result.result == {"value": "done"}
    # 3 status polls + 1 result fetch.
    assert len(mock_client.calls) == 4
    assert sleep_mock.call_count >= 2


def test_wait_raises_mesh_job_timeout_after_budget(mock_client) -> None:
    """wait() never blocks longer than ``timeout`` seconds. When the budget
    expires before the job reaches a terminal state, raise
    MeshJobTimeoutError."""
    from kamiwaza_sdk.exceptions import MeshJobTimeoutError
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    job_id = "job-stuck"
    mock_client.expect("GET", f"/cluster/jobs/{job_id}/status", {"status": "RUNNING"})

    fake_now = [0.0]

    def fake_monotonic() -> float:
        return fake_now[0]

    def fake_sleep(seconds: float) -> None:
        fake_now[0] += seconds

    with (
        patch("time.monotonic", side_effect=fake_monotonic),
        patch("time.sleep", side_effect=fake_sleep),
        pytest.raises(MeshJobTimeoutError),
    ):
        JobsAPI(client=mock_client).wait(job_id, timeout=10)


def test_wait_returns_failed_job_result(mock_client) -> None:
    """When the job reaches FAILED, wait() returns the JobResult with
    status=FAILED — does NOT raise. Customers branch on result.status."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    job_id = "job-fail"
    mock_client.expect("GET", f"/cluster/jobs/{job_id}/status", {"status": "FAILED"})
    mock_client.expect(
        "GET",
        f"/cluster/jobs/{job_id}/result",
        {"job_id": job_id, "status": "FAILED", "error": "TypeError: bad arg"},
    )

    result = JobsAPI(client=mock_client).wait(job_id, timeout=60)

    assert result.status == "FAILED"
    assert result.error == "TypeError: bad arg"


def test_wait_returns_canceled_job_result(mock_client) -> None:
    """CANCELED is terminal — return the JobResult, don't raise."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    job_id = "job-canceled-1"
    mock_client.expect("GET", f"/cluster/jobs/{job_id}/status", {"status": "CANCELED"})
    mock_client.expect(
        "GET",
        f"/cluster/jobs/{job_id}/result",
        {"job_id": job_id, "status": "CANCELED", "error": None},
    )

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).wait(job_id, timeout=60)

    assert result.status == "CANCELED"
    assert result.job_id == job_id


def test_run_omits_target_cluster_from_body_when_local(mock_client) -> None:
    """When ``target_cluster`` is not provided, the SDK must not include
    a ``target_cluster`` key in the request body. The server reads the
    presence of that key to route — silently passing ``None`` would
    change semantics."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    mock_client.expect(
        "POST",
        "/cluster/jobs/run",
        {"job_id": "local-1", "status": "SUCCEEDED", "result": "ok"},
    )

    JobsAPI(client=mock_client).run(entrypoint="python script.py")

    _method, _path, kwargs = mock_client.calls[0]
    body = kwargs.get("json", {})
    assert body == {"entrypoint": "python script.py"}
    assert "target_cluster" not in body
    assert "runtime_env" not in body
    assert "timeout_seconds" not in body
