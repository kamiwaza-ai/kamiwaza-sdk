from __future__ import annotations

from typing import Any

import pytest

from kamiwaza_sdk.agent_tools.workflows import (
    WORKFLOWS,
    AppRequest,
    DatasetTarget,
    GatePackageRef,
    Refusal,
    WorkflowSpec,
    complete_dataset_ingestion,
    deploy_app_from_garden,
    diagnose_deployment,
    find_and_deploy_model,
    install_and_bind_gate_package,
    preflight_and_deploy_model,
    retire_deployment,
)

pytestmark = pytest.mark.unit


class Recorder:
    """A stand-in service that records calls and returns canned values."""

    def __init__(self, calls: list[str], prefix: str, **returns: Any) -> None:
        self._calls = calls
        self._prefix = prefix
        self._returns = returns

    def __getattr__(self, name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            self._calls.append(f"{self._prefix}.{name}")
            value = self._returns.get(name)
            return value() if callable(value) else value

        return call


class FakeInstance:
    def __init__(self, host: str | None = "node-1", port: int | None = 8080) -> None:
        self.host_name = host
        self.port = port
        self.status = "RUNNING"


class FakeDeployment:
    def __init__(self, status: str = "DEPLOYED") -> None:
        self.id = "dep-1"
        self.status = status


def _client(calls: list[str], **services: Any) -> Any:
    return type("FakeClient", (), services)()


def test_all_workflows_are_registered() -> None:
    assert len(WORKFLOWS) == 17


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_no_workflow_stacks_two_waits_or_two_approvals(name: str) -> None:
    """FR-015 and FR-017, as a per-workflow assertion.

    The spec fields are singular by construction — one polling step, one
    approval step — so a workflow that grows a second of either cannot express
    it here and has to become two tools instead.
    """
    spec = WORKFLOWS[name]
    assert isinstance(spec.polling_step, (str, type(None)))
    assert isinstance(spec.approval_step, (str, type(None)))


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_every_workflow_names_its_terminal_artifact(name: str) -> None:
    """FR-014: the final usable thing, not a handle to chase."""
    spec = WORKFLOWS[name]
    assert spec.terminal_artifact.strip()
    assert spec.summary.strip().endswith(".")


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_a_non_idempotent_workflow_says_why(name: str) -> None:
    """FR-016: not idempotent is allowed; silently not idempotent is not."""
    spec = WORKFLOWS[name]
    if not spec.idempotent:
        assert spec.not_idempotent_because
        assert len(spec.not_idempotent_because.split()) >= 8


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_a_polling_workflow_names_how_to_resume(name: str) -> None:
    """FR-016 and SC-003: an expired wait must be resumable, never retried."""
    spec = WORKFLOWS[name]
    if spec.polling_step:
        assert spec.resume_hint


def test_the_spec_refuses_a_silent_non_idempotent_workflow() -> None:
    with pytest.raises(ValueError, match="must say why"):
        WorkflowSpec(
            name="bad",
            summary="Do a thing.",
            terminal_artifact="A thing.",
            polling_step=None,
            approval_step=None,
            idempotent=False,
        )


def test_the_spec_refuses_a_wait_with_no_resume_path() -> None:
    with pytest.raises(ValueError, match="resume"):
        WorkflowSpec(
            name="bad",
            summary="Do a thing.",
            terminal_artifact="A thing.",
            polling_step="Waiting.",
            approval_step=None,
            idempotent=True,
        )


def test_no_workflow_wraps_a_single_destructive_act() -> None:
    """FR-018: deletion, credential rotation and uninstall stay explicit.

    `retire_deployment` is the near miss and is deliberately allowed: it stops
    a deployment and then confirms nothing remains, which is the confirmation
    a bare stop does not give.
    """
    forbidden = ("delete", "rotate", "uninstall", "purge", "revoke")
    for name in WORKFLOWS:
        assert not any(word in name for word in forbidden), (
            f"{name} wraps a single destructive act; it must stay a plain call"
        )


def test_find_and_deploy_refuses_when_nothing_matches() -> None:
    """An empty search is not a deployment failure and must not read as one."""
    calls: list[str] = []
    client = _client(
        calls, models=Recorder(calls, "models", search_models=lambda: [])
    )
    outcome = find_and_deploy_model(client, "no such model")
    assert isinstance(outcome, Refusal)
    assert "no such model" in outcome.shortfall
    assert "serving.deploy_model" not in calls, "refused but deployed anyway"


def test_find_and_deploy_returns_the_endpoint_not_a_handle() -> None:
    calls: list[str] = []
    model = type("Model", (), {"id": "m-1"})()
    client = _client(
        calls,
        models=Recorder(calls, "models", search_models=lambda: [model]),
        serving=Recorder(
            calls,
            "serving",
            deploy_model=FakeDeployment,
            wait_deployment_ready=FakeDeployment,
            list_model_instances=lambda: [FakeInstance()],
        ),
    )
    outcome = find_and_deploy_model(client, "llama")
    assert outcome.endpoint == "http://node-1:8080"
    assert outcome.deployment_id == "dep-1"


def test_preflight_refuses_before_creating_anything() -> None:
    """The whole point: a capacity failure after creation leaves a half-built
    deployment an agent has to find and clean up."""
    calls: list[str] = []
    fields = {"m_id": "m-1", "m_config_id": None, "m_file_id": None}
    request = type("Request", (), fields)()
    client = _client(
        calls,
        serving=Recorder(
            calls, "serving", estimate_model_vram=lambda: {"required_bytes": 40_000}
        ),
        cluster=Recorder(calls, "cluster", get_running_nodes=lambda: []),
    )
    outcome = preflight_and_deploy_model(client, request)
    assert isinstance(outcome, Refusal)
    assert "40000 bytes" in outcome.shortfall
    assert "serving.deploy_model" not in calls


def test_retire_confirms_rather_than_trusting_the_stop() -> None:
    """A stop that succeeds while an instance lingers has not freed capacity."""
    calls: list[str] = []
    client = _client(
        calls,
        serving=Recorder(
            calls,
            "serving",
            stop_deployment=lambda: True,
            list_model_instances=lambda: [FakeInstance()],
        ),
    )
    result = retire_deployment(client, "dep-1")
    assert result["stopped"] is True
    assert result["remaining_instances"] == 1, "the confirmation must be reported"
    assert calls == ["serving.stop_deployment", "serving.list_model_instances"]


@pytest.mark.parametrize(
    ("instances", "expected"),
    [
        ([FakeInstance()], "serving and reachable"),
        ([], "no instances"),
        ([FakeInstance(host=None, port=None)], "none reported a reachable endpoint"),
    ],
)
def test_diagnose_returns_a_conclusion_not_a_log_dump(instances, expected) -> None:
    calls: list[str] = []
    client = _client(
        calls,
        serving=Recorder(
            calls,
            "serving",
            get_deployment_status=FakeDeployment,
            list_model_instances=lambda: instances,
        ),
    )
    result = diagnose_deployment(client, "dep-1")
    assert expected in result["conclusion"]
    assert result["next_action"]
    assert "logs" not in result


def test_promotion_refuses_while_staging_is_unfinished() -> None:
    calls: list[str] = []
    status = type("Status", (), {"status": "RUNNING"})()
    client = _client(
        calls, ingestion=Recorder(calls, "ingestion", get_job_status=lambda: status)
    )
    target = DatasetTarget(name="sales", platform="snowflake")
    outcome = complete_dataset_ingestion(client, "job-1", target)
    assert isinstance(outcome, Refusal)
    assert "running" in outcome.shortfall
    assert "catalog.create_dataset" not in calls


def test_gate_package_install_reads_the_binding_back() -> None:
    """Installing without confirming leaves an agent believing an unverified
    package is bound."""
    calls: list[str] = []
    client = _client(
        calls,
        gates=type(
            "Gates",
            (),
            {
                "packages": Recorder(
                    calls,
                    "gates.packages",
                    install=lambda: type("R", (), {"status": "installed"})(),
                    get=lambda: {"name": "policy", "hash": "abc"},
                )
            },
        )(),
    )
    package = GatePackageRef(name="policy", spec="policy==1.0", hash_digest="abc")
    result = install_and_bind_gate_package(client, package)
    assert result["binding"] == {"name": "policy", "hash": "abc"}
    assert calls == ["gates.packages.install", "gates.packages.get"]


def test_app_deploy_refuses_an_unknown_garden_app() -> None:
    calls: list[str] = []
    client = _client(
        calls, apps=Recorder(calls, "apps", find_template=lambda: None)
    )
    outcome = deploy_app_from_garden(client, AppRequest(name="nonexistent"))
    assert isinstance(outcome, Refusal)
    assert "apps.install_by_name" not in calls
