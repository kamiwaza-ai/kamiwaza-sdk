"""T5.22 / ENG-4699 — JobsAPI.run(recoverable=True) on canonical surface.

WS-M3.2 test migration (T7.15 / ENG-5049). Per design §4.2.14: when
``recoverable=True``, the SDK uses async submit + poll instead of the
sync /run path so the job_id is in the SDK's hands immediately — a
connection drop mid-job is recoverable via ``kz.jobs.wait(job_id, ...)``.
"""

from __future__ import annotations

from unittest.mock import patch


def test_run_recoverable_false_uses_sync_endpoint(mock_client) -> None:
    """Default ``recoverable=False`` hits POST /cluster/jobs/run."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    mock_client.expect(
        "POST",
        "/cluster/jobs/run",
        {"job_id": "job-123", "status": "SUCCEEDED", "result": {"answer": 42}},
    )

    result = JobsAPI(client=mock_client).run(entrypoint="python query.py")
    assert result.status == "SUCCEEDED"
    assert result.job_id == "job-123"


def test_run_recoverable_true_uses_submit_then_poll(mock_client) -> None:
    """``recoverable=True`` hits submit then polls status + result. Critical
    property: the job_id is available in the SDK after submit (before the
    long poll loop completes) — a connection drop mid-poll is recoverable
    from a saved job_id."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    job_id = "job-recoverable-xyz"
    mock_client.expect("POST", "/cluster/jobs/submit", {"job_id": job_id})
    mock_client.expect("GET", f"/cluster/jobs/{job_id}/status", {"status": "SUCCEEDED"})
    mock_client.expect(
        "GET",
        f"/cluster/jobs/{job_id}/result",
        {"job_id": job_id, "status": "SUCCEEDED", "result": {"answer": 42}},
    )

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).run(
            entrypoint="python query.py",
            recoverable=True,
            timeout_seconds=300,
        )

    assert result.job_id == job_id
    assert result.status == "SUCCEEDED"


def test_run_recoverable_true_forwards_runtime_env_to_submit(mock_client) -> None:
    """runtime_env, target_cluster, timeout_seconds must reach the submit
    body — they're how the server provisions the long-running job."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    job_id = "job-fwd-test"
    mock_client.expect("POST", "/cluster/jobs/submit", {"job_id": job_id})
    mock_client.expect("GET", f"/cluster/jobs/{job_id}/status", {"status": "SUCCEEDED"})
    mock_client.expect(
        "GET",
        f"/cluster/jobs/{job_id}/result",
        {"job_id": job_id, "status": "SUCCEEDED", "result": {}},
    )

    with patch("time.sleep"):
        JobsAPI(client=mock_client).run(
            entrypoint="python long.py",
            target_cluster="ORION",
            runtime_env={"env_vars": {"X": "1"}},
            timeout_seconds=300,
            recoverable=True,
        )

    submit_call = next(c for c in mock_client.calls if c[0] == "POST")
    body = submit_call[2].get("json", {})
    assert body["entrypoint"] == "python long.py"
    assert body["target_cluster"] == "ORION"
    assert body["runtime_env"] == {"env_vars": {"X": "1"}}
    assert body["timeout_seconds"] == 300
