"""T5.9 / ENG-4680 — kamiwaza.jobs module skeleton tests.

Skeleton scope per design §6.2 WS-M1 T5.9:
    - kz.jobs.run(target_cluster, entrypoint, ...) -> JobResult
    - kz.jobs.submit_async(...) -> str (job_id)
    - kz.jobs.wait(job_id, timeout=...) -> JobResult

cancel + recoverable=True land in WS-M2 (T5.22 / T5.35), not here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


def test_kamiwaza_exposes_jobs_attribute() -> None:
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    assert client.jobs is not None


def test_jobs_is_lazy_loaded() -> None:
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    assert client.jobs is client.jobs


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_run_synchronous_returns_job_result(httpx_mock: Any) -> None:
    """kz.jobs.run(target_cluster, entrypoint, ...) hits the synchronous
    /api/cluster/jobs/run endpoint, which returns the completed JobResult
    in-line (no separate poll). target_cluster is a federation name; SDK
    encodes routing in the request body, server side dispatches via mesh."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import JobResult

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/jobs/run",
        status_code=200,
        json={
            "job_id": "job-abc-123",
            "status": "SUCCEEDED",
            "result": {"answer": "42"},
            "audit_actor": "cdr-baker@LYRA",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.jobs.run(
        target_cluster="ORION",
        entrypoint="python -c 'print(42)'",
    )

    assert isinstance(result, JobResult)
    assert result.status == "SUCCEEDED"
    assert result.audit_actor == "cdr-baker@LYRA"

    sent = httpx_mock.get_requests()[0]
    body = sent.read()
    assert b"ORION" in body
    assert b"entrypoint" in body


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_run_local_when_target_cluster_omitted(httpx_mock: Any) -> None:
    """When target_cluster is not provided, the job runs on the local
    cluster. Wire shape doesn't include target_cluster."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/jobs/run",
        status_code=200,
        json={
            "job_id": "job-local",
            "status": "SUCCEEDED",
            "result": {"local": True},
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.jobs.run(entrypoint="python -c 'pass'")

    assert result.status == "SUCCEEDED"

    sent = httpx_mock.get_requests()[0]
    body = sent.read()
    assert b"target_cluster" not in body


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_submit_async_returns_job_id(httpx_mock: Any) -> None:
    """submit_async POSTs to /api/cluster/jobs/submit and returns just
    the job_id immediately. Customer polls via wait()."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/jobs/submit",
        status_code=202,
        json={"job_id": "job-async-456"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    job_id = client.jobs.submit_async(
        target_cluster="ORION",
        entrypoint="python big_job.py",
    )

    assert job_id == "job-async-456"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_wait_polls_until_terminal_state(httpx_mock: Any) -> None:
    """wait() polls /api/cluster/jobs/{id}/status with backoff until the
    status is terminal (SUCCEEDED/FAILED/STOPPED/CANCELED), then fetches
    the result via /api/cluster/jobs/{id}/result and returns JobResult.
    """
    from kamiwaza.client import Kamiwaza

    job_id = "job-wait-789"

    # Two PENDING/RUNNING then SUCCEEDED.
    httpx_mock.add_response(
        method="GET",
        url=f"https://kamiwaza.test/api/cluster/jobs/{job_id}/status",
        status_code=200,
        json={"status": "PENDING"},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"https://kamiwaza.test/api/cluster/jobs/{job_id}/status",
        status_code=200,
        json={"status": "RUNNING"},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"https://kamiwaza.test/api/cluster/jobs/{job_id}/status",
        status_code=200,
        json={"status": "SUCCEEDED"},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"https://kamiwaza.test/api/cluster/jobs/{job_id}/result",
        status_code=200,
        json={
            "job_id": job_id,
            "status": "SUCCEEDED",
            "result": {"value": "done"},
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    with patch("time.sleep") as sleep_mock:
        result = client.jobs.wait(job_id, timeout=60)

    assert result.status == "SUCCEEDED"
    assert result.result == {"value": "done"}
    # Polled status thrice + fetched result once.
    assert len(httpx_mock.get_requests()) == 4
    assert sleep_mock.call_count >= 2


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_wait_raises_mesh_job_timeout_after_budget(
    httpx_mock: Any,
) -> None:
    """wait() never blocks longer than `timeout` seconds. When the budget
    expires before the job reaches a terminal state, raise
    MeshJobTimeoutError so customer code can branch on it."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import MeshJobTimeoutError

    job_id = "job-stuck"

    for _ in range(50):
        httpx_mock.add_response(
            method="GET",
            url=f"https://kamiwaza.test/api/cluster/jobs/{job_id}/status",
            status_code=200,
            json={"status": "RUNNING"},
        )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    fake_now = [0.0]

    def fake_monotonic() -> float:
        return fake_now[0]

    def fake_sleep(seconds: float) -> None:
        fake_now[0] += seconds

    with patch("time.monotonic", side_effect=fake_monotonic):
        with patch("time.sleep", side_effect=fake_sleep):
            with pytest.raises(MeshJobTimeoutError):
                client.jobs.wait(job_id, timeout=10)


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_wait_returns_failed_job_result(httpx_mock: Any) -> None:
    """When the job reaches FAILED, wait() returns the JobResult with
    status=FAILED — does NOT raise. Customers branch on result.status."""
    from kamiwaza.client import Kamiwaza

    job_id = "job-fail"

    httpx_mock.add_response(
        method="GET",
        url=f"https://kamiwaza.test/api/cluster/jobs/{job_id}/status",
        status_code=200,
        json={"status": "FAILED"},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"https://kamiwaza.test/api/cluster/jobs/{job_id}/result",
        status_code=200,
        json={
            "job_id": job_id,
            "status": "FAILED",
            "error": "TypeError: bad arg",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.jobs.wait(job_id, timeout=60)

    assert result.status == "FAILED"
    assert result.error == "TypeError: bad arg"
