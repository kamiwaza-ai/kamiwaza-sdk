"""Model deployment workflows.

The chains an operator runs most: find a model and serve it, serve a known one,
check capacity first, work out why one is not serving, and retire it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from kamiwaza_sdk.schemas.serving.serving import CreateModelDeployment

from ._contract import DeploymentOutcome, Refusal, WorkflowSpec, register

__all__ = [
    "deploy_and_connect_model",
    "diagnose_deployment",
    "find_and_deploy_model",
    "preflight_and_deploy_model",
    "retire_deployment",
]


def endpoint_of(instances: list[Any]) -> str | None:
    """Return the first reachable endpoint among deployment instances.

    ``listen_port`` is the field :class:`~kamiwaza_sdk.schemas.serving.serving.ModelInstance`
    declares and the field a live platform returns. ``port`` is read as well,
    and second, because nothing in the schema promises it — this used to read
    ``port`` only, so every deployment workflow reported no endpoint at all
    against a real cluster while the instance in front of it was serving on
    ``listen_port: 8080``. Found by a persona audit against a live deployment,
    where an operator asking for an endpoint is the whole point of the warm
    path (FR-014, Story 2).

    Args:
        instances: Model instances from the platform.

    Returns:
        The first instance's host and port as a URL, or ``None`` when no
        instance reports one.
    """
    for instance in instances:
        host = getattr(instance, "host_name", None) or getattr(instance, "host", None)
        port = getattr(instance, "listen_port", None) or getattr(instance, "port", None)
        if host and port:
            return f"http://{host}:{port}"
    return None


def _await_deployment(
    client: Any, deployment: Any, model_id: str, timeout_seconds: int
) -> DeploymentOutcome | Refusal:
    """Wait for a deployment to report ready and collect its endpoint.

    The single polling step shared by the deployment workflows. Keeping it in
    one place is what makes "at most one wait per workflow" checkable.

    Args:
        client: The platform client.
        deployment: The deployment just created.
        model_id: Identifier of the model deployed.
        timeout_seconds: Bound on the wait.

    Returns:
        The deployment with its instances and endpoint, or a refusal when the
        platform did not accept the deploy.
    """
    # ``ServingService.deploy_model`` returns ``Union[UUID, bool]`` and answers
    # False when the platform refuses the request. That False used to flow into
    # ``wait_deployment_ready``, which resolves its argument with
    # ``UUID(str(deployment_id))`` — so a refused deploy surfaced as a status
    # lookup on the id False rather than as a refusal.
    if deployment is False:
        return Refusal(
            reason="The platform refused the deploy request.",
            shortfall="a deployment the platform accepted",
        )
    deployment_id = getattr(deployment, "id", deployment)
    ready = client.serving.wait_deployment_ready(
        deployment_id, timeout_seconds=timeout_seconds
    )
    instances = client.serving.list_model_instances(
        deployment_id=getattr(ready, "id", deployment_id)
    )
    return DeploymentOutcome(
        deployment_id=str(getattr(ready, "id", deployment_id)),
        model=model_id,
        endpoint=endpoint_of(list(instances)),
        instances=list(instances),
    )


@register(
    WorkflowSpec(
        name="find_and_deploy_model",
        summary="Find a model by name and deploy it, returning the ready deployment.",
        terminal_artifact="A ready deployment with its identifier and endpoint.",
        polling_step="Waiting for the deployment to report ready.",
        approval_step="Creating the deployment.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Calling it twice creates a second deployment of the same model. "
            "Check list_active_deployments_serving first, or deploy by id."
        ),
        resume_hint="get_deployment_status_serving",
    )
)
def find_and_deploy_model(
    client: Any, query: str, *, timeout_seconds: int = 3600
) -> DeploymentOutcome | Refusal:
    """Find a model by search term and deploy the first match.

    Args:
        client: The platform client.
        query: Search term matched against catalogued models.
        timeout_seconds: Bound on the readiness wait.

    Returns:
        The ready deployment, or a refusal — nothing matched the search term,
        or the platform declined the deploy.
    """
    matches = client.models.search_models(query, limit=1)
    # An empty search result is not a deployment failure, and reporting it as
    # one sends an agent retrying a term that will never match. This rationale
    # sat in the docstring, which every listing pays for: 30 tokens.
    if not matches:
        return Refusal(
            reason="No catalogued model matches that search term.",
            shortfall=f"a model matching {query!r}",
        )
    model = matches[0]
    deployment = client.serving.deploy_model(model_id=model.id, wait=False)
    return _await_deployment(client, deployment, str(model.id), timeout_seconds)


@register(
    WorkflowSpec(
        name="deploy_and_connect_model",
        summary="Deploy a known model and return an endpoint ready for inference.",
        terminal_artifact="A ready deployment with a reachable endpoint.",
        polling_step="Waiting for the deployment to report ready.",
        approval_step="Creating the deployment.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Each call creates a deployment. Reuse an existing one by id "
            "instead of calling again."
        ),
        resume_hint="get_deployment_status_serving",
    )
)
def deploy_and_connect_model(
    client: Any, model_id: str, *, timeout_seconds: int = 3600
) -> DeploymentOutcome | Refusal:
    """Deploy a model by identifier and return its endpoint.

    Args:
        client: The platform client.
        model_id: Identifier of an already-catalogued model.
        timeout_seconds: Bound on the readiness wait.

    Returns:
        The ready deployment and its endpoint, or a refusal when the platform
        declined the deploy.
    """
    deployment = client.serving.deploy_model(model_id=model_id, wait=False)
    return _await_deployment(client, deployment, model_id, timeout_seconds)


#: What the platform names the requirement in a VRAM estimate. The estimator
#: builds ``computed_vram_estimate`` as a float of bytes
#: (``kamiwaza/serving/vram_estimator.py``, ``_build_result``), and that is the
#: key the live endpoint test asserts. The rest are earlier names, kept so an
#: older platform still gates instead of skipping the check.
_REQUIRED_KEYS = (
    "computed_vram_estimate",
    "required_bytes",
    "vram_bytes",
    "total_bytes",
    "estimated_bytes",
)

#: The capacity figure the same estimate carries: accelerator memory on the
#: largest node the platform can see. The platform gates its own deployment on
#: exactly this comparison — ``vram_exceeds_total`` in
#: ``kamiwaza/serving/resource_allocation.py`` is
#: ``computed_vram_estimate > highest_node_vram > 0``.
_CAPACITY_KEYS = ("highest_node_vram",)


def _estimate_bytes(estimate: Any, keys: tuple[str, ...]) -> int | None:
    """Return the first byte figure an estimate reports under ``keys``.

    Args:
        estimate: The platform's estimate, whose key naming is not fixed by a
            model this package owns.
        keys: Candidate keys, most current first.

    Returns:
        The figure in bytes, or ``None`` when the estimate carries none.
        ``None`` means "unknown", never "zero" — treating an absent estimate as
        no requirement would deploy into a cluster that cannot hold it. A float
        counts: the platform reports both figures as floats, and reading ints
        only left every real estimate unknown and the capacity gate dead.
    """
    if not isinstance(estimate, dict):
        return None
    for key in keys:
        value = estimate.get(key)
        # bool is an int in Python, and a flag under one of these keys is not a
        # byte count.
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _capacity_refusal(
    *,
    required: int | None,
    largest_node: int | None,
    nodes: Any,
) -> Refusal | None:
    """Whether the cluster can hold this deployment, and why not when it cannot.

    Held apart from the workflow because it is the whole decision the workflow
    exists to make, and reading it inline put two multi-term conditions in the
    middle of a function that otherwise reads as estimate, check, deploy.

    Args:
        required: Bytes of accelerator memory the estimate asks for, or
            ``None`` when the estimate reported none.
        largest_node: The largest per-node total the estimate reports, or
            ``None``.
        nodes: The running nodes the cluster reports.

    Returns:
        The refusal to answer with, or ``None`` when the deployment may go
        ahead.

        Three cases, and the order matters. An unknown requirement never
        refuses: the estimator is the only thing that knows, and refusing on
        its silence would block every deployment it cannot price. A known
        requirement with no running node refuses, because nothing can hold it.
        A known requirement larger than the largest node's total refuses — the
        same rule the platform applies in ``resource_allocation.py``, which
        skips the comparison when the capacity figure is zero, as it is for a
        CPU-only cluster and for the estimator's all-zero default.

        What this cannot see is how much of a node another deployment already
        holds: the figure is a node total, and nothing in the estimate or the
        node list reports free memory. So this catches a requirement no node
        could ever hold, and the platform can still refuse later on memory that
        is already in use.
    """
    if required is None:
        return None
    if not nodes:
        return Refusal(
            reason="No running node can host this deployment.",
            shortfall=f"{required} bytes of accelerator memory, 0 running nodes",
        )
    if largest_node and required > largest_node:
        return Refusal(
            reason="No running node has enough accelerator memory for this deployment.",
            shortfall=(
                f"{required} bytes of accelerator memory; the largest node "
                f"reports {largest_node} bytes in total"
            ),
        )
    return None


@register(
    WorkflowSpec(
        name="preflight_and_deploy_model",
        summary="Check capacity for a model deployment and deploy only if it fits.",
        terminal_artifact="A ready deployment, or a refusal naming the shortfall.",
        polling_step="Waiting for the deployment to report ready.",
        approval_step="Creating the deployment.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "The deployment step creates. The preflight alone is a read and "
            "can be repeated safely."
        ),
        resume_hint="get_deployment_status_serving",
    )
)
def preflight_and_deploy_model(
    client: Any,
    model_id: str,
    model_config_id: str,
    *,
    model_file_id: str | None = None,
    timeout_seconds: int = 3600,
) -> DeploymentOutcome | Refusal:
    """Estimate memory, compare it against the cluster, then deploy or refuse.

    Refusing before anything is created is the point: a capacity failure
    afterwards leaves a half-built deployment to find and clean up.

    The figure compared is the largest node's *total* accelerator memory, not
    its free memory. So a refusal means no node could ever hold this, and a
    deployment that passes can still be refused later on memory in use.

    Args:
        client: The platform client.
        model_id: Model to deploy.
        model_config_id: Configuration to deploy it with.
        model_file_id: Specific model file, when the model has more than one.
        timeout_seconds: Bound on the readiness wait.

    Returns:
        The ready deployment, or a :class:`Refusal` naming the requirement and
        the largest node figure.
    """
    # Three identifiers rather than a ``CreateModelDeployment``: the request
    # model has sixteen fields and this reads three, so accepting the whole
    # thing published 720 tokens of schema on every listing and invited a
    # caller to set thirteen fields that would be ignored. ``model_config_id``
    # is required because the platform's own request requires it — defaulting
    # it would fail at the platform instead of at the argument.
    #
    # That leaves five parameters, one past what a static analyser recommends.
    # Deliberate: this signature *is* the published tool shape, so grouping
    # arguments into an object would put a nested definition back into every
    # listing to satisfy a threshold written for ordinary call sites.
    deployment_request = CreateModelDeployment(
        m_id=UUID(str(model_id)),
        m_config_id=UUID(str(model_config_id)),
        m_file_id=UUID(str(model_file_id)) if model_file_id else None,
    )
    estimate = client.serving.estimate_model_vram(deployment_request)
    refusal = _capacity_refusal(
        required=_estimate_bytes(estimate, _REQUIRED_KEYS),
        largest_node=_estimate_bytes(estimate, _CAPACITY_KEYS),
        nodes=client.cluster.get_running_nodes(),
    )
    if refusal is not None:
        return refusal
    deployment = client.serving.deploy_model(
        model_id=model_id,
        m_config_id=model_config_id,
        m_file_id=model_file_id,
        wait=False,
    )
    return _await_deployment(client, deployment, str(model_id), timeout_seconds)


@register(
    WorkflowSpec(
        name="diagnose_deployment",
        summary="Report why a deployment is not serving, with the evidence behind it.",
        terminal_artifact="A conclusion, its evidence, and the next action.",
        polling_step=None,
        approval_step=None,
        idempotent=True,
        reads_only=True,
        destructive=False,
    )
)
def diagnose_deployment(client: Any, deployment_id: str) -> dict[str, Any]:
    """Diagnose a deployment and return a bounded conclusion.

    Returns a conclusion and the facts behind it, never a raw log dump: an
    agent handed 10,000 log lines spends its context and still has to guess.

    Args:
        client: The platform client.
        deployment_id: Identifier of the deployment.

    Returns:
        Mapping with ``conclusion``, ``evidence`` and ``next_action``.
    """
    # `get_deployment`, not `get_deployment_status`: the platform answers
    # `/serving/deployment/{id}/status` with a bare JSON string — measured,
    # `"DEPLOYED"` — while that method validates the response into a
    # `ModelDeployment`, so it raised a pydantic ValidationError for every
    # deployment that exists and this diagnosis could never succeed. Found by a
    # persona audit driving a live cluster: the operator's one headline tool
    # failed on a healthy deployment, and the failure was reported as a
    # platform fault worth retrying. `/serving/deployment/{id}` returns the
    # whole object, whose `status` is the same string with the rest of the
    # facts beside it.
    deployment = client.serving.get_deployment(deployment_id)
    instances = list(client.serving.list_model_instances(deployment_id=deployment_id))
    evidence = {
        "status": getattr(deployment, "status", None),
        "instances": len(instances),
        "instance_statuses": [getattr(i, "status", None) for i in instances],
    }
    if instances and endpoint_of(instances):
        return {
            "conclusion": "The deployment is serving and reachable.",
            "evidence": evidence,
            "next_action": "None. Call the endpoint.",
        }
    if not instances:
        return {
            "conclusion": "The deployment exists but has no instances.",
            "evidence": evidence,
            "next_action": "Check cluster capacity with diagnose_cluster.",
        }
    return {
        "conclusion": "Instances exist but none reported a reachable endpoint.",
        "evidence": evidence,
        "next_action": "Wait for readiness, then re-run this diagnosis.",
    }


@register(
    WorkflowSpec(
        name="retire_deployment",
        summary="Stop a deployment and confirm no instances remain.",
        terminal_artifact=(
            "The released deployment id and the confirmed instance count."
        ),
        polling_step=None,
        approval_step="Stopping the deployment.",
        idempotent=True,
        reads_only=False,
        destructive=True,
    )
)
def retire_deployment(client: Any, deployment_id: str) -> dict[str, Any]:
    """Stop a deployment, then confirm nothing is left running.

    The confirmation is the reason this is a workflow rather than a plain stop:
    a stop that reports success while an instance lingers leaves an agent
    believing capacity was freed when it was not.

    Args:
        client: The platform client.
        deployment_id: Identifier of the deployment to stop.

    Returns:
        Mapping with ``deployment_id``, ``stopped`` and ``remaining_instances``.
    """
    stopped = client.serving.stop_deployment(deployment_id=deployment_id)
    remaining = client.serving.list_model_instances(deployment_id=deployment_id)
    return {
        "deployment_id": deployment_id,
        "stopped": bool(stopped),
        "remaining_instances": len(list(remaining)),
    }
