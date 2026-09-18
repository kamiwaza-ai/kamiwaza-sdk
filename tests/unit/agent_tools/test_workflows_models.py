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
asked to wait, returns the ``UUID`` (or the ``False``) its real signature
declares, and the node objects are real
:class:`~kamiwaza_sdk.schemas.cluster.NodeListNode` instances, so a field that
does not exist fails the test rather than returning a canned value.

Two more a service-level fake cannot see, so the last test here drives a real
:class:`~kamiwaza_sdk.client.KamiwazaClient` with only ``requests.Session.send``
replaced:

* ``estimate_model_vram`` posted a raw ``model_dump()`` of a request carrying
  ``UUID`` fields, so every call raised ``TypeError`` in ``requests`` before a
  byte was sent;
* a refused deploy answers ``False``, which was passed on as a deployment id.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
import requests

from kamiwaza_sdk.agent_tools.workflows import (
    Refusal,
    deploy_and_connect_model,
    find_and_deploy_model,
    preflight_and_deploy_model,
)
from kamiwaza_sdk.client import KamiwazaClient
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
    (``services/serving.py:61-62,150-155``). Raising here is that hour, made
    observable: a workflow that leaves ``wait`` at its default has already lost
    the caller's bound by the time it reaches ``_await_deployment``.

    It also returns what the real method returns: a ``UUID``, or ``False`` when
    the platform refuses the deploy (``Union[UUID, bool]``,
    ``services/serving.py:64,148``). It used to answer with a
    ``ModelDeployment``, a type that method never produces, which is why the
    refusal path went unnoticed.
    """

    def __init__(
        self,
        *,
        estimate: dict[str, Any] | None = None,
        deployment: ModelDeployment | None = None,
        refuse: bool = False,
    ) -> None:
        self.deploy_calls: list[dict[str, Any]] = []
        self.wait_calls: list[tuple[Any, int]] = []
        self._estimate = estimate if estimate is not None else {}
        self._deployment = deployment if deployment is not None else _deployment()
        self._refuse = refuse

    def estimate_model_vram(self, deployment_request: Any) -> dict[str, Any]:
        """Return the canned estimate, recording nothing: it is a plain read."""
        return self._estimate

    def deploy_model(self, **kwargs: Any) -> UUID | bool:
        """Record the deploy call, refusing to be the thing that waits."""
        self.deploy_calls.append(kwargs)
        if kwargs.get("wait", True):
            raise TimeoutError(
                "the service waited for up to 3600s; the caller's bound never applied"
            )
        if self._refuse:
            return False
        return self._deployment.id

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


def test_a_refused_deploy_answers_with_a_refusal() -> None:
    """Low #9: ``deploy_model`` answers False when the platform refuses.

    That False used to be handed to ``wait_deployment_ready``, which resolves
    its argument with ``UUID(str(deployment_id))`` — so a refused deploy was
    reported as a status lookup on the id ``False`` rather than as a refusal.
    """
    serving = FakeServing(refuse=True)

    outcome = deploy_and_connect_model(FakeClient(serving), MODEL_ID)

    assert isinstance(outcome, Refusal)
    assert "refused" in outcome.reason
    assert serving.wait_calls == [], "refused, then polled for readiness anyway"


def test_find_and_deploy_reports_a_refused_deploy_as_a_refusal() -> None:
    """The same False, at the search-then-deploy call site."""
    serving = FakeServing(refuse=True)
    model = type("Model", (), {"id": MODEL_ID})()

    outcome = find_and_deploy_model(
        FakeClient(serving, models=FakeModels([model])), "llama"
    )

    assert isinstance(outcome, Refusal)
    assert serving.wait_calls == []


def _canned_send(
    routes: dict[tuple[str, str], Any], recorded: list[Any]
) -> Any:
    """Return a ``requests.Session.send`` replacement that answers from ``routes``.

    The transport is the only thing stubbed: the real client prepares the
    request, so the body reaching ``send`` is the body the platform would have
    received — including whatever ``json=`` had to be serialised through.
    """

    def send(_session: Any, request: Any, **_kwargs: Any) -> requests.Response:
        recorded.append(request)
        payload = routes[(request.method, urlparse(request.url).path)]
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(payload).encode()
        response.url = request.url
        response.request = request
        return response

    return send


def test_preflight_and_deploy_model_reaches_the_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """High #1: the estimate request has to be serialisable to be sent at all.

    Driven through a real ``KamiwazaClient`` with only ``requests.Session.send``
    replaced, because the defect lives in ``ServingService.estimate_model_vram``
    and a service-level fake never runs that body. The workflow builds a
    ``CreateModelDeployment`` carrying ``UUID`` fields; the method posted a raw
    ``model_dump()``, so ``requests`` raised ``TypeError: Object of type UUID is
    not JSON serializable`` while preparing the body and the capacity gate below
    it never ran.
    """
    deployment = _deployment()
    instance = _instance(deployment.id)
    routes: dict[tuple[str, str], Any] = {
        ("POST", "/api/serving/estimate_model_vram"): {
            "computed_vram_estimate": 8.0 * GIB,
            "highest_node_vram": 24.0 * GIB,
            "estimation_source": "measured",
        },
        ("GET", "/api/cluster/get_running_nodes"): [
            {"node_id": "node-1", "alive": True, "node_ip": "10.0.0.1"}
        ],
        ("POST", "/api/serving/deploy_model"): str(deployment.id),
        ("GET", f"/api/serving/deployment/{deployment.id}"): deployment.model_dump(
            mode="json"
        ),
        ("GET", "/api/serving/model_instances"): [instance.model_dump(mode="json")],
    }
    recorded: list[Any] = []
    monkeypatch.setattr(
        requests.Session, "send", _canned_send(routes, recorded), raising=True
    )
    client = KamiwazaClient(base_url="https://kamiwaza.test/api", api_key="pat-test")

    outcome = preflight_and_deploy_model(client, MODEL_ID, CONFIG_ID, timeout_seconds=1)

    assert not isinstance(outcome, Refusal), "8 GiB fits a 24 GiB node"
    assert outcome.endpoint == "http://node-1:8080"
    estimate_bodies = [
        json.loads(request.body)
        for request in recorded
        if urlparse(request.url).path.endswith("/estimate_model_vram")
    ]
    assert estimate_bodies, "the estimate never reached the transport"
    assert estimate_bodies[0]["m_id"] == MODEL_ID
    assert estimate_bodies[0]["m_config_id"] == CONFIG_ID
    assert estimate_bodies[0]["m_file_id"] is None
