from __future__ import annotations

import inspect
import re

from typing import Any

import pytest

from kamiwaza_sdk.agent_tools import workflows as workflow_module
from kamiwaza_sdk.agent_tools.descriptors import _READ_VERBS
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
    enclave_ingest,
    find_and_deploy_model,
    install_and_bind_gate_package,
    preflight_and_deploy_model,
    rag_query,
    retire_deployment,
)
from kamiwaza_sdk.agent_tools.workflows._contract import search_workflows

from kamiwaza_sdk.agent_tools.workflows.models import endpoint_of

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
    """A model instance shaped like the platform's own.

    ``listen_port`` is what :class:`ModelInstance` declares and what a live
    cluster returns; this stub used to declare ``port`` instead, which is why
    the suite passed while every deployment workflow reported no endpoint
    against a real platform. A stub that disagrees with the schema tests the
    stub.
    """

    def __init__(
        self,
        host: str | None = "node-1",
        listen_port: int | None = 8080,
        port: int | None = None,
    ) -> None:
        self.host_name = host
        self.listen_port = listen_port
        if port is not None:
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
    client = _client(
        calls,
        serving=Recorder(
            calls, "serving", estimate_model_vram=lambda: {"required_bytes": 40_000}
        ),
        cluster=Recorder(calls, "cluster", get_running_nodes=lambda: []),
    )
    outcome = preflight_and_deploy_model(
        client,
        "8b1f4bd0-0000-4000-8000-000000000001",
        "8b1f4bd0-0000-4000-8000-000000000002",
    )
    assert isinstance(outcome, Refusal)
    assert "40000 bytes" in outcome.shortfall
    assert "serving.deploy_model" not in calls


def test_a_workflow_takes_its_arguments_not_a_platform_request_body() -> None:
    """The property that keeps a workflow tool usable, and cheap to publish.

    ``CreateModelDeployment`` has sixteen fields and this workflow reads three.
    Accepting the whole model published 720 tokens of schema on every listing
    and invited a caller to set thirteen fields that would be ignored — and
    for the retrieval workflow, two of the fields it would have published are a
    credential and a session, which must never appear on an agent surface.

    Asserted over the parameter names rather than the token count, because the
    rule is what matters: a workflow's arguments are the task's, and building
    the platform's request body is the workflow's own job.
    """
    for workflow in (preflight_and_deploy_model, rag_query, enclave_ingest):
        parameters = inspect.signature(workflow).parameters
        for name, parameter in parameters.items():
            if name == "client":
                continue
            annotation = str(parameter.annotation)
            assert not any(
                model in annotation
                for model in (
                    "CreateModelDeployment",
                    "RetrievalRequest",
                    "ConnectorCreate",
                    "RelationshipTuple",
                )
            ), (
                f"{workflow.__name__} takes {name!r} as a platform request "
                f"model; take the fields the workflow uses instead"
            )
            assert annotation != "typing.Any", (
                f"{workflow.__name__} takes {name!r} as Any, which publishes an "
                f"empty schema — a caller cannot tell what to pass"
            )


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
        (
            [FakeInstance(host=None, listen_port=None)],
            "none reported a reachable endpoint",
        ),
    ],
)
def test_diagnose_returns_a_conclusion_not_a_log_dump(instances, expected) -> None:
    calls: list[str] = []
    client = _client(
        calls,
        serving=Recorder(
            calls,
            "serving",
            get_deployment=FakeDeployment,
            list_model_instances=lambda: instances,
        ),
    )
    result = diagnose_deployment(client, "dep-1")
    assert expected in result["conclusion"]
    assert result["next_action"]
    assert "logs" not in result
    assert "serving.get_deployment_status" not in calls, (
        "the platform answers that route with a bare status string, which the "
        "client package's ModelDeployment rejects: reading it made this "
        "diagnosis raise a pydantic error for every deployment that exists"
    )


def test_an_endpoint_is_read_from_the_field_the_platform_publishes() -> None:
    """``listen_port`` first, ``port`` second, and one of them is enough.

    A live instance returns ``host_name`` and ``listen_port`` and no ``port``
    at all. Reading ``port`` only meant every deployment workflow answered
    "no endpoint" while the instance in front of it was serving — which is
    the one thing an operator asked for.
    """
    assert endpoint_of([FakeInstance()]) == "http://node-1:8080"
    assert (
        endpoint_of([FakeInstance(listen_port=None, port=9001)])
        == "http://node-1:9001"
    )
    assert endpoint_of([FakeInstance(host=None, listen_port=None)]) is None


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


#: Matches a ``client.<service>.<method>(`` call in a workflow body, including
#: a nested service such as ``client.enclaves.connectors.create``.
_CLIENT_CALL = re.compile(r"client\.[a-z_][\w.]*?\.([a-z_]+)\(")


def test_a_read_only_workflow_needs_no_approval_and_ends_nothing() -> None:
    """FR-006a: the two hints a host acts on must not contradict each other.

    Asserted as a property rather than a table of expected values per
    workflow, because a table is the same data twice and passes for a
    wrong-but-consistent pair.
    """
    for name, spec in WORKFLOWS.items():
        assert not (spec.reads_only and spec.destructive), (
            f"{name} reads only and is destructive; one of the two is wrong"
        )
        assert not (spec.reads_only and spec.approval_step), (
            f"{name} reads only but names an approval step, and approval "
            f"exists to gate a change"
        )


def test_a_read_only_workflow_calls_only_read_methods() -> None:
    """The declaration has to match the body, not the name.

    ``find_and_deploy_model`` leads with a read verb and deploys a model, so a
    name is no evidence at all. This is the assertion that catches a workflow
    marked read-only because of what it is called.
    """
    read_only = [name for name, spec in WORKFLOWS.items() if spec.reads_only]
    assert read_only, "no workflow declares reads_only; the declaration went missing"
    for name in read_only:
        source = inspect.getsource(getattr(workflow_module, name))
        methods = _CLIENT_CALL.findall(source)
        assert methods, f"{name} declares reads_only but calls the client nowhere"
        for method in methods:
            assert method.split("_")[0] in _READ_VERBS, (
                f"{name} declares reads_only but calls {method!r}, which "
                f"changes something"
            )


def test_the_spec_refuses_a_read_only_workflow_that_is_also_destructive() -> None:
    with pytest.raises(ValueError, match="contradictory_hints"):
        WorkflowSpec(
            name="contradictory_hints",
            summary="Do a thing.",
            terminal_artifact="A thing.",
            polling_step=None,
            approval_step=None,
            idempotent=True,
            reads_only=True,
            destructive=True,
        )


def test_the_spec_refuses_an_approval_step_on_a_read_only_workflow() -> None:
    with pytest.raises(ValueError, match="gated_read"):
        WorkflowSpec(
            name="gated_read",
            summary="Do a thing.",
            terminal_artifact="A thing.",
            polling_step=None,
            approval_step="Reading the thing.",
            idempotent=True,
            reads_only=True,
        )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("diagnose deployment", "diagnose_deployment"),
        ("why is my deployment not serving", "diagnose_deployment"),
        ("get rows from a dataset", "rag_query"),
        ("create a workroom", "create_workroom_and_enter"),
        ("deploy an app from the garden", "deploy_app_from_garden"),
        # "connect" reads two ways on this platform: pairing two clusters and
        # reaching a deployment. Both must stay findable.
        (
            "connect this cluster to our partner's cluster",
            "pair_federation_and_allow_user",
        ),
        ("deploy a model and connect to it", "deploy_and_connect_model"),
    ],
)
def test_a_workflow_is_found_by_the_words_a_caller_uses(
    query: str, expected: str
) -> None:
    """A workflow an agent cannot find is a workflow it reimplements by hand.

    Each query is the goal stated the way a caller states it, not the tool's
    own name, because the tool's name is what a caller searching does not
    know.
    """
    found = search_workflows(query)

    assert found, f"{query!r} found no workflow"
    assert found[0].name == expected


def test_a_query_matching_no_workflow_returns_nothing() -> None:
    """FR-040: the honest answer, not the least-bad workflow."""
    assert search_workflows("xylophone repair scheduling") == ()
    assert search_workflows("   ") == ()
    # This phrase used to return rag_query, and the match was the defect:
    # rag_query takes no question and answers none, it materialises rows from
    # a dataset. A caller with a question wants the context search and
    # retrieve operations, which no workflow collapses. Finding nothing here
    # is the truthful answer, so it is pinned rather than left to drift back.
    assert search_workflows("ask a question over my documents") == ()


def test_the_search_limit_is_honoured() -> None:
    """Three workflows deploy a model, so this query has a list to cut."""
    assert len(search_workflows("deploy a model")) > 1
    assert len(search_workflows("deploy a model", limit=1)) == 1
