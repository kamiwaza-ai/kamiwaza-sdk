"""T5.38 / ENG-4715 — JobsAPI.run runtime_env convenience kwargs.

WS-M3.2 test migration (T7.15 / ENG-5049). Per design §4.2.11 + FR-94:
demo authors shouldn't have to construct the Ray runtime_env dict by
hand for the common cases (pip deps, local py_modules, working_dir
bundling). The SDK packs these into runtime_env on the wire.
"""

from __future__ import annotations

from unittest.mock import patch


def test_run_packs_pip_kwarg_into_runtime_env(mock_client) -> None:
    """pip=["numpy"] becomes runtime_env={"pip": ["numpy"]}."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    mock_client.expect(
        "POST",
        "/cluster/jobs/run",
        {"job_id": "j1", "status": "SUCCEEDED"},
    )

    JobsAPI(client=mock_client).run(
        entrypoint="python q.py", pip=["numpy", "scipy==1.10"]
    )

    body = mock_client.calls[0][2].get("json", {})
    assert body["runtime_env"] == {"pip": ["numpy", "scipy==1.10"]}


def test_run_packs_py_modules_and_working_dir(mock_client) -> None:
    """py_modules + working_dir + pip combine into one runtime_env dict."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    mock_client.expect(
        "POST", "/cluster/jobs/run", {"job_id": "j2", "status": "SUCCEEDED"}
    )

    JobsAPI(client=mock_client).run(
        entrypoint="python q.py",
        pip=["numpy"],
        py_modules=["./my_module"],
        working_dir="./project",
    )

    body = mock_client.calls[0][2].get("json", {})
    assert body["runtime_env"] == {
        "pip": ["numpy"],
        "py_modules": ["./my_module"],
        "working_dir": "./project",
    }


def test_run_explicit_runtime_env_takes_precedence_over_convenience_kwargs(
    mock_client,
) -> None:
    """Caller-provided runtime_env keys win on collision — convenience
    kwargs are sugar; the explicit dict is the wire-shape contract."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    mock_client.expect(
        "POST", "/cluster/jobs/run", {"job_id": "j3", "status": "SUCCEEDED"}
    )

    JobsAPI(client=mock_client).run(
        entrypoint="python q.py",
        pip=["numpy"],
        runtime_env={"pip": ["explicit-pin==1.0"], "env_vars": {"X": "1"}},
    )

    body = mock_client.calls[0][2].get("json", {})
    assert body["runtime_env"]["pip"] == ["explicit-pin==1.0"]
    assert body["runtime_env"]["env_vars"] == {"X": "1"}


def test_run_omits_runtime_env_when_neither_form_provided(mock_client) -> None:
    """No runtime_env in body when caller provides neither."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    mock_client.expect(
        "POST", "/cluster/jobs/run", {"job_id": "j4", "status": "SUCCEEDED"}
    )

    JobsAPI(client=mock_client).run(entrypoint="python q.py")

    body = mock_client.calls[0][2].get("json", {})
    assert "runtime_env" not in body


def test_convenience_kwargs_work_on_recoverable_path_too(mock_client) -> None:
    """recoverable=True route uses submit; same wire body shape."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    job_id = "j5"
    mock_client.expect("POST", "/cluster/jobs/submit", {"job_id": job_id})
    mock_client.expect("GET", f"/cluster/jobs/{job_id}/status", {"status": "SUCCEEDED"})
    mock_client.expect(
        "GET",
        f"/cluster/jobs/{job_id}/result",
        {"job_id": job_id, "status": "SUCCEEDED", "result": {}},
    )

    with patch("time.sleep"):
        JobsAPI(client=mock_client).run(
            entrypoint="python q.py",
            pip=["numpy"],
            recoverable=True,
        )

    submit_call = next(c for c in mock_client.calls if c[0] == "POST")
    body = submit_call[2].get("json", {})
    assert body["runtime_env"] == {"pip": ["numpy"]}
