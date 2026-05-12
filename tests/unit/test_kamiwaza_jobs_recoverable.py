"""T5.22 / ENG-4699 — kz.jobs.run(recoverable=True) tests.

Per design `system-design.md` §4.2.14: when ``recoverable=True``, the
SDK uses async submit + poll instead of the sync /run path. This puts
the ``job_id`` in the SDK's hands immediately so a connection drop
mid-job is recoverable via ``kz.jobs.wait(job_id, ...)``.

Demo bullet (8): a 5-minute federated job submitted via
``kz.jobs.run(..., recoverable=True)`` survives an induced mid-call SDK
process kill — the SDK reconnects via job_id and retrieves the result.

Walking-skeleton scope: verify the dispatch routing (recoverable=True →
submit+poll path; recoverable=False → sync /run path). The connection-
drop recovery shape is exercised by the integration test (T5.24).
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_run_recoverable_false_uses_sync_endpoint(httpx_mock: Any) -> None:
    """Default ``recoverable=False`` hits POST /api/cluster/jobs/run."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/jobs/run",
        status_code=200,
        json={
            "job_id": "job-123",
            "status": "SUCCEEDED",
            "result": {"answer": 42},
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.jobs.run(entrypoint="python query.py")

    assert result.status == "SUCCEEDED"
    assert result.job_id == "job-123"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_run_recoverable_true_uses_submit_then_poll(httpx_mock: Any) -> None:
    """``recoverable=True`` hits submit then polls status + result.

    Critical property: the job_id is available in the SDK after the
    submit call (i.e., before the long poll loop completes). This is the
    load-bearing reason for the recoverable path — a connection drop
    mid-poll is recoverable from a saved job_id.
    """
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/jobs/submit",
        status_code=200,
        json={"job_id": "job-recoverable-xyz"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs/job-recoverable-xyz/status",
        status_code=200,
        json={"status": "SUCCEEDED"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs/job-recoverable-xyz/result",
        status_code=200,
        json={
            "job_id": "job-recoverable-xyz",
            "status": "SUCCEEDED",
            "result": {"answer": 42},
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.jobs.run(
        entrypoint="python query.py",
        recoverable=True,
        timeout_seconds=300,
    )

    assert result.job_id == "job-recoverable-xyz"
    assert result.status == "SUCCEEDED"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_run_recoverable_true_forwards_runtime_env_to_submit(
    httpx_mock: Any,
) -> None:
    """runtime_env, target_cluster, timeout_seconds must reach the submit
    body — they're how the server provisions the long-running job."""
    from kamiwaza.client import Kamiwaza

    captured_request = httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/jobs/submit",
        status_code=200,
        json={"job_id": "job-fwd-test"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs/job-fwd-test/status",
        status_code=200,
        json={"status": "SUCCEEDED"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs/job-fwd-test/result",
        status_code=200,
        json={"job_id": "job-fwd-test", "status": "SUCCEEDED", "result": {}},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.jobs.run(
        entrypoint="python long.py",
        target_cluster="ORION",
        runtime_env={"env_vars": {"X": "1"}},
        timeout_seconds=300,
        recoverable=True,
    )

    # First request is the submit; assert its body shape.
    submit_req = httpx_mock.get_requests(method="POST")[0]
    import json

    body = json.loads(submit_req.content)
    assert body["entrypoint"] == "python long.py"
    assert body["target_cluster"] == "ORION"
    assert body["runtime_env"] == {"env_vars": {"X": "1"}}
    assert body["timeout_seconds"] == 300
    # Suppress unused-variable diag — fixture-injection side-effect captured above.
    _ = captured_request
