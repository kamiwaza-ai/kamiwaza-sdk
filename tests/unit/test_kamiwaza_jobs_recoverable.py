"""T5.22 / ENG-4699 — JobsAPI.run(recoverable=True) on canonical surface.

WS-M3.2 test migration (T7.15 / ENG-5049). Per design §4.2.14: when
``recoverable=True``, the SDK uses async submit + poll instead of the
sync /run path so the job_id is in the SDK's hands immediately — a
connection drop mid-job is recoverable via ``kz.jobs.wait(job_id, ...)``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


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
    """Runtime configuration reaches the remote submit without a routing hint."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    job_id = "job-fwd-test"
    prefix = "/mesh/ORION/api/cluster/jobs"
    mock_client.expect("POST", f"{prefix}/submit", {"job_id": job_id})
    mock_client.expect("GET", f"{prefix}/{job_id}/status", {"status": "SUCCEEDED"})
    mock_client.expect(
        "GET",
        f"{prefix}/{job_id}/result",
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
    assert "target_cluster" not in body
    assert body["runtime_env"] == {"env_vars": {"X": "1"}}
    assert body["timeout_seconds"] == 300


# --- wait() /result parsing (ENG-7284): /result returns the job's
# KZ_MESH_RUN_ON_JSON:: marker payload, NOT a JobResult; status is
# authoritative from /status; a 410 (no marker) is tolerated. -------------


def _status_terminal(mock_client, job_id: str) -> None:
    mock_client.expect("GET", f"/cluster/jobs/{job_id}/status", {"status": "SUCCEEDED"})


def test_wait_tolerates_410_result_returns_status_only(mock_client) -> None:
    """A job that emits no marker → /result 410; status is still authoritative."""
    from kamiwaza_sdk.exceptions import APIError
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    jid = "job-410"
    _status_terminal(mock_client, jid)
    mock_client.raise_on(
        "GET", f"/cluster/jobs/{jid}/result", APIError("logs expired", status_code=410)
    )

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).wait(jid, timeout=300)

    assert result.status == "SUCCEEDED"
    assert result.job_id == jid
    assert result.result is None


def test_wait_promotes_audit_actor_and_nests_bare_marker(mock_client) -> None:
    """A bare marker (no job_id+status wrapper) is the job's domain output: it
    nests under .result, with audit_actor — the OBO-identity bridge — the sole
    field promoted to top level."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    jid = "job-marker"
    _status_terminal(mock_client, jid)
    mock_client.expect(
        "GET",
        f"/cluster/jobs/{jid}/result",
        {"audit_actor": "alice@cluster-a", "probe": "eng7284"},
    )

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).wait(jid, timeout=300)

    assert result.job_id == jid
    assert result.status == "SUCCEEDED"
    assert result.audit_actor == "alice@cluster-a"  # the sole promoted field
    assert result.result == {"probe": "eng7284"}  # domain output nested
    assert getattr(result, "probe", None) is None  # NOT a top-level extra


def test_wait_nests_generic_marker_payload_under_result(mock_client) -> None:
    """A structured job output like {"answer": 42} must land in .result, honoring
    the README contract — not as a top-level extra leaving .result None."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    jid = "job-answer"
    _status_terminal(mock_client, jid)
    mock_client.expect("GET", f"/cluster/jobs/{jid}/result", {"answer": 42})

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).wait(jid, timeout=300)

    assert result.result == {"answer": 42}
    assert getattr(result, "answer", None) is None


def test_wait_promotes_jobresult_shaped_error(mock_client) -> None:
    """When /result is JobResult-shaped (a FAILED job's {job_id,status,error}),
    declared fields promote — error must NOT be buried under .result. Locks the
    contract that the {"answer": 42} nesting fix must not regress."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    jid = "job-err-shaped"
    mock_client.expect("GET", f"/cluster/jobs/{jid}/status", {"status": "FAILED"})
    mock_client.expect(
        "GET",
        f"/cluster/jobs/{jid}/result",
        {"job_id": jid, "status": "FAILED", "error": "TypeError: bad arg"},
    )

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).wait(jid, timeout=300)

    assert result.status == "FAILED"
    assert result.error == "TypeError: bad arg"  # promoted, not nested
    assert result.result is None


def test_wait_authoritative_status_shadows_marker(mock_client) -> None:
    """A marker carrying job_id/status must NOT override the authoritative poll
    values: job_id + status are injected last so a colliding marker key cannot
    shadow them."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    jid = "job-real"
    _status_terminal(mock_client, jid)
    mock_client.expect(
        "GET",
        f"/cluster/jobs/{jid}/result",
        {"job_id": "WRONG", "status": "FAILED", "audit_actor": "u"},
    )

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).wait(jid, timeout=300)

    assert result.job_id == jid  # not "WRONG"
    assert result.status == "SUCCEEDED"  # /status wins, not the marker's "FAILED"
    assert result.audit_actor == "u"  # declared field → promoted


def test_wait_propagates_non_410_result_error(mock_client) -> None:
    """Only 410 is tolerated; any other /result error propagates."""
    from kamiwaza_sdk.exceptions import APIError
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    jid = "job-500"
    _status_terminal(mock_client, jid)
    mock_client.raise_on(
        "GET", f"/cluster/jobs/{jid}/result", APIError("server error", status_code=500)
    )

    with patch("time.sleep"), pytest.raises(APIError):
        JobsAPI(client=mock_client).wait(jid, timeout=300)


def test_wait_preserves_extras_on_jobresult_shaped_body(mock_client) -> None:
    """A JobResult-shaped /result (job_id + status present) carrying an
    undeclared forward-compat field keeps that field as a top-level extra
    (extra="allow"), NOT nested under .result — preserves the SDK forward-compat
    contract (ENG-7284 review Med#1)."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    jid = "job-fwd-compat"
    mock_client.expect("GET", f"/cluster/jobs/{jid}/status", {"status": "FAILED"})
    mock_client.expect(
        "GET",
        f"/cluster/jobs/{jid}/result",
        {"job_id": jid, "status": "FAILED", "error": "boom", "log_url": "s3://logs/x"},
    )

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).wait(jid, timeout=300)

    assert result.error == "boom"  # declared → promoted
    assert getattr(result, "log_url", None) == "s3://logs/x"  # extra → top level
    assert result.result is None  # NOT nested for a JobResult-shaped body


def test_wait_wraps_nondict_result_body(mock_client) -> None:
    """A non-dict /result body wraps under .result rather than failing
    validation (locks the documented wrap branch)."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    jid = "job-list-result"
    _status_terminal(mock_client, jid)
    mock_client.expect("GET", f"/cluster/jobs/{jid}/result", [1, 2, 3])

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).wait(jid, timeout=300)

    assert result.result == [1, 2, 3]
    assert result.status == "SUCCEEDED"


# --- Bare-marker field-collision regressions (ENG-7284 review High #1). A
# job's domain output key that happens to be named like a JobResult field must
# NOT be promoted+validated (would raise) or dropped — it stays opaque under
# .result. -----------------------------------------------------------------


def test_wait_bare_marker_error_dict_does_not_crash(mock_client) -> None:
    """High#1(a): a bare marker with a non-string ``error`` key must not be
    promoted to JobResult.error (Optional[str]) — that raised ValidationError,
    crashing a SUCCEEDED job. It nests, opaque."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    jid = "job-err-dict"
    _status_terminal(mock_client, jid)
    mock_client.expect(
        "GET", f"/cluster/jobs/{jid}/result", {"error": {"code": 1}, "answer": 42}
    )

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).wait(jid, timeout=300)

    assert result.status == "SUCCEEDED"  # no crash
    assert result.error is None  # not promoted from domain output
    assert result.result == {"error": {"code": 1}, "answer": 42}


def test_wait_bare_marker_status_key_preserved(mock_client) -> None:
    """High#1(b): a bare marker's own ``status`` output must survive under
    .result, not be promoted then overwritten by the authoritative poll."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    jid = "job-status-key"
    _status_terminal(mock_client, jid)
    mock_client.expect(
        "GET", f"/cluster/jobs/{jid}/result", {"status": "ok", "answer": 42}
    )

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).wait(jid, timeout=300)

    assert result.status == "SUCCEEDED"  # authoritative poll value
    assert result.result == {"status": "ok", "answer": 42}  # job output intact


def test_wait_bare_marker_result_key_not_dropped(mock_client) -> None:
    """High#1(c): a bare marker with its own ``result`` key must not cause
    sibling domain keys to be dropped."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    jid = "job-result-key"
    _status_terminal(mock_client, jid)
    mock_client.expect(
        "GET", f"/cluster/jobs/{jid}/result", {"result": "primary", "answer": 42}
    )

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).wait(jid, timeout=300)

    assert result.result == {"result": "primary", "answer": 42}  # answer kept


def test_wait_bare_marker_non_string_audit_actor_stays_nested(mock_client) -> None:
    """Only a *string* audit_actor is promoted; a colliding non-string value
    stays opaque under .result rather than raising on the str field."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    jid = "job-actor-dict"
    _status_terminal(mock_client, jid)
    mock_client.expect(
        "GET", f"/cluster/jobs/{jid}/result", {"audit_actor": {"id": 1}, "answer": 42}
    )

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).wait(jid, timeout=300)

    assert result.status == "SUCCEEDED"  # no crash
    assert result.audit_actor is None  # non-string → not promoted
    assert result.result == {"audit_actor": {"id": 1}, "answer": 42}
