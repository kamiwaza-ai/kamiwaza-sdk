"""Regression tests for the model deployment workflows.

Two defects the shared ``Recorder`` fake in ``test_workflows.py`` could not
catch, because it answers any attribute with a canned value and never checks
the arguments it was handed:

* every deployment workflow called ``serving.deploy_model`` with its default
  ``wait=True``, so the service blocked in ``wait_deployment_ready`` with its
  own ``timeout_seconds=3600`` before the workflow's bounded wait ever ran;
* ``preflight_and_deploy_model`` refused only when zero nodes were running, so
  it never compared the requirement against any node's memory.

The fakes here mirror the real services instead: ``deploy_model`` raises when
asked to wait, and the node objects are real
:class:`~kamiwaza_sdk.schemas.cluster.NodeListNode` instances, so a field that
does not exist fails the test rather than returning a canned value.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from kamiwaza_sdk.agent_tools.workflows import (
    Refusal,
    deploy_and_connect_model,
    find_and_deploy_model,
    preflight_and_deploy_model,
)
from kamiwaza_sdk.schemas.cluster import NodeListNode
from kamiwaza_sdk.schemas.serving.serving import ModelDeployment, ModelInstance

pytestmark = pytest.mark.unit

MODEL_ID = "8b1f4bd0-0000-4000-8000-000000000001"
CONFIG_ID = "8b1f4bd0-0000-4000-8000-000000000002"
GIB = 1024**3


def _deployment() -> ModelDeployment:
    """Return a deployment as the platform's own schema declares it."""
    return ModelDeployment(
        id=UUID("8b1f4bd0-0000-4000-8000-00000000000d"),
        m_id=UUID(MODEL_ID),
        m_config_id=UUID(CONFIG_ID),
        requested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        status="DEPLOYED",
    )


def _instance(deployment_id: UUID) -> ModelInstance:
    """Return a serving instance as the platform's own schema declares it."""
    return ModelInstance(
        id=uuid4(),
        deployment_id=deployment_id,
        deployed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        host_name="node-1",
        listen_port=8080,
        status="DEPLOYED",
    )


class FakeServing:
    """A serving service that holds ``deploy_model`` to its real contract.

    ``ServingService.deploy_model`` defaults to ``wait=True`` and then blocks in
    ``wait_deployment_ready`` for up to its own ``timeout_seconds=3600``
    (``services/serving.py:54-55,144-151``). Raising here is that hour, made
    observable: a workflow that leaves ``wait`` at its default has already lost
    the caller's bound by the time it reaches ``_await_deployment``.
    """

    def __init__(
        self,
        *,
        estimate: dict[str, Any] | None = None,
        deployment: ModelDeployment | None = None,
    ) -> None:
        self.deploy_calls: list[dict[str, Any]] = []
        self.wait_calls: list[tuple[Any, int]] = []
        self._estimate = estimate if estimate is not None else {}
        self._deployment = deployment if deployment is not None else _deployment()

    def estimate_model_vram(self, deployment_request: Any) -> dict[str, Any]:
        """Return the canned estimate, recording nothing: it is a plain read."""
        return self._estimate

    def deploy_model(self, **kwargs: Any) -> ModelDeployment:
        """Record the deploy call, refusing to be the thing that waits."""
        self.deploy_calls.append(kwargs)
        if kwargs.get("wait", True):
            raise TimeoutError(
                "the service waited for up to 3600s; the caller's bound never applied"
            )
        return self._deployment

    def wait_deployment_ready(
        self, deployment_id: Any, *, timeout_seconds: int
    ) -> ModelDeployment:
        """Record the one bounded wait the workflow is allowed."""
        self.wait_calls.append((deployment_id, timeout_seconds))
        return self._deployment

    def list_model_instances(self, deployment_id: Any) -> list[ModelInstance]:
        """Return one serving instance for the deployment."""
        return [_instance(self._deployment.id)]


class FakeCluster:
    """A cluster service returning real ``NodeListNode`` objects."""

    def __init__(self, nodes: list[NodeListNode]) -> None:
        self._nodes = nodes

    def get_running_nodes(self) -> list[NodeListNode]:
        """Return the running nodes, as ``ClusterService`` does."""
        return self._nodes


class FakeModels:
    """A models service returning one search match."""

    def __init__(self, matches: list[Any]) -> None:
        self._matches = matches

    def search_models(self, query: str, limit: int | None = None) -> list[Any]:
        """Return the canned matches for any query."""
        return self._matches


class FakeClient:
    """The three services the model workflows reach for."""

    def __init__(
        self,
        serving: FakeServing,
        *,
        cluster: FakeCluster | None = None,
        models: FakeModels | None = None,
    ) -> None:
        self.serving = serving
        self.cluster = cluster if cluster is not None else FakeCluster([])
        self.models = models if models is not None else FakeModels([])


def _node(node_id: str) -> NodeListNode:
    """Return a running node, built from the schema the cluster read returns."""
    return NodeListNode(node_id=node_id, alive=True, node_ip="10.0.0.1")


def test_deploy_and_connect_model_leaves_the_wait_to_the_workflow() -> None:
    """High #4: one wait, and the caller's ``timeout_seconds`` bounds it."""
    serving = FakeServing()
    outcome = deploy_and_connect_model(FakeClient(serving), MODEL_ID, timeout_seconds=60)

    assert serving.deploy_calls == [{"model_id": MODEL_ID, "wait": False}]
    assert [timeout for _, timeout in serving.wait_calls] == [60]
    assert outcome.endpoint == "http://node-1:8080"


def test_find_and_deploy_model_leaves_the_wait_to_the_workflow() -> None:
    """The same property at the search-then-deploy call site."""
    serving = FakeServing()
    model = type("Model", (), {"id": MODEL_ID})()
    client = FakeClient(serving, models=FakeModels([model]))

    outcome = find_and_deploy_model(client, "llama", timeout_seconds=90)

    assert serving.deploy_calls == [{"model_id": MODEL_ID, "wait": False}]
    assert [timeout for _, timeout in serving.wait_calls] == [90]
    assert not isinstance(outcome, Refusal)


def test_preflight_and_deploy_model_leaves_the_wait_to_the_workflow() -> None:
    """The same property at the preflight call site, which fits and deploys."""
    serving = FakeServing(
        estimate={
            "computed_vram_estimate": 8.0 * GIB,
            "highest_node_vram": 24.0 * GIB,
            "estimation_source": "measured",
        }
    )
    client = FakeClient(serving, cluster=FakeCluster([_node("node-1")]))

    outcome = preflight_and_deploy_model(
        client, MODEL_ID, CONFIG_ID, timeout_seconds=120
    )

    assert not isinstance(outcome, Refusal), "8 GiB fits a 24 GiB node"
    assert serving.deploy_calls == [
        {
            "model_id": MODEL_ID,
            "m_config_id": CONFIG_ID,
            "m_file_id": None,
            "wait": False,
        }
    ]
    assert [timeout for _, timeout in serving.wait_calls] == [120]


def test_preflight_refuses_when_no_node_is_large_enough() -> None:
    """High #5: a running node is not a node with room.

    80 GiB needed against a 24 GiB largest node, with two nodes alive. The
    zero-node branch cannot fire, so only a real comparison refuses here.
    """
    serving = FakeServing(
        estimate={
            "computed_vram_estimate": 80.0 * GIB,
            "highest_node_vram": 24.0 * GIB,
            "num_gpus": 1,
            "estimation_source": "measured",
        }
    )
    client = FakeClient(
        serving, cluster=FakeCluster([_node("node-1"), _node("node-2")])
    )

    outcome = preflight_and_deploy_model(client, MODEL_ID, CONFIG_ID)

    assert isinstance(outcome, Refusal)
    assert str(80 * GIB) in outcome.shortfall
    assert str(24 * GIB) in outcome.shortfall, "the refusal must name what it saw"
    assert serving.deploy_calls == [], "refused but deployed anyway"


def test_preflight_deploys_when_the_platform_reports_no_accelerator() -> None:
    """A zero capacity figure is a CPU-only cluster, not a shortfall.

    The estimator answers with an all-zero default when a model carries no
    size metadata, and the platform skips its own comparison in that case
    (``highest_node_vram > 0``). Refusing here would block every CPU
    deployment.
    """
    serving = FakeServing(
        estimate={
            "computed_vram_estimate": 4.0 * GIB,
            "highest_node_vram": 0.0,
            "num_gpus": 0,
            "estimation_source": "default",
        }
    )
    client = FakeClient(serving, cluster=FakeCluster([_node("node-1")]))

    outcome = preflight_and_deploy_model(client, MODEL_ID, CONFIG_ID)

    assert not isinstance(outcome, Refusal)
    assert len(serving.deploy_calls) == 1
