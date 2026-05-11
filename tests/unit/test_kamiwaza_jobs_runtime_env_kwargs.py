"""T5.38 / ENG-4715 — JobsAPI.run runtime_env convenience kwargs.

Per design §4.2.11 + FR-94: demo authors shouldn't have to construct
the Ray runtime_env dict by hand for the common cases (pip deps, local
py_modules, working_dir bundling). The SDK packs these into runtime_env
on the wire.

When both runtime_env and one of the convenience kwargs are passed, the
convenience kwargs merge into runtime_env (caller-provided runtime_env
takes precedence on key collision — caller is explicit about wire shape).
"""

from __future__ import annotations

import json
from typing import Any

import pytest


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_run_packs_pip_kwarg_into_runtime_env(httpx_mock: Any) -> None:
    """pip=["numpy"] becomes runtime_env={"pip": ["numpy"]}."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/jobs/run",
        status_code=200,
        json={"job_id": "j1", "status": "SUCCEEDED"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat")
    client.jobs.run(entrypoint="python q.py", pip=["numpy", "scipy==1.10"])

    request = httpx_mock.get_requests(method="POST")[0]
    body = json.loads(request.content)
    assert body["runtime_env"] == {"pip": ["numpy", "scipy==1.10"]}


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_run_packs_py_modules_and_working_dir(httpx_mock: Any) -> None:
    """py_modules + working_dir + pip combine into one runtime_env dict."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/jobs/run",
        status_code=200,
        json={"job_id": "j2", "status": "SUCCEEDED"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat")
    client.jobs.run(
        entrypoint="python q.py",
        pip=["numpy"],
        py_modules=["./my_module"],
        working_dir="./project",
    )

    request = httpx_mock.get_requests(method="POST")[0]
    body = json.loads(request.content)
    assert body["runtime_env"] == {
        "pip": ["numpy"],
        "py_modules": ["./my_module"],
        "working_dir": "./project",
    }


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_run_explicit_runtime_env_takes_precedence_over_convenience_kwargs(
    httpx_mock: Any,
) -> None:
    """Caller-provided runtime_env keys win on collision.

    Convenience kwargs are sugar; if the caller passed an explicit
    runtime_env, they're being deliberate about wire shape and we don't
    silently overwrite their fields.
    """
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/jobs/run",
        status_code=200,
        json={"job_id": "j3", "status": "SUCCEEDED"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat")
    client.jobs.run(
        entrypoint="python q.py",
        pip=["numpy"],  # convenience
        runtime_env={"pip": ["explicit-pin==1.0"], "env_vars": {"X": "1"}},
    )

    request = httpx_mock.get_requests(method="POST")[0]
    body = json.loads(request.content)
    # Caller's runtime_env.pip wins; env_vars carried through.
    assert body["runtime_env"]["pip"] == ["explicit-pin==1.0"]
    assert body["runtime_env"]["env_vars"] == {"X": "1"}


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_run_omits_runtime_env_when_neither_form_provided(
    httpx_mock: Any,
) -> None:
    """No runtime_env in body when caller provides neither — preserves
    pre-T5.38 wire shape for default callers."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/jobs/run",
        status_code=200,
        json={"job_id": "j4", "status": "SUCCEEDED"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat")
    client.jobs.run(entrypoint="python q.py")

    request = httpx_mock.get_requests(method="POST")[0]
    body = json.loads(request.content)
    assert "runtime_env" not in body


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_convenience_kwargs_work_on_recoverable_path_too(
    httpx_mock: Any,
) -> None:
    """recoverable=True route uses submit, but the wire body shape must
    match — convenience kwargs pack the same way."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/jobs/submit",
        status_code=200,
        json={"job_id": "j5"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs/j5/status",
        status_code=200,
        json={"status": "SUCCEEDED"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs/j5/result",
        status_code=200,
        json={"job_id": "j5", "status": "SUCCEEDED", "result": {}},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat")
    client.jobs.run(
        entrypoint="python q.py",
        pip=["numpy"],
        recoverable=True,
    )

    submit_req = httpx_mock.get_requests(method="POST")[0]
    body = json.loads(submit_req.content)
    assert body["runtime_env"] == {"pip": ["numpy"]}
