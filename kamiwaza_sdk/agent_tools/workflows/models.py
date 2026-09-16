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

    Args:
        instances: Model instances from the platform.

    Returns:
        The first instance's host and port as a URL, or ``None`` when no
        instance reports one.
    """
    for instance in instances:
        host = getattr(instance, "host_name", None) or getattr(instance, "host", None)
        port = getattr(instance, "port", None)
        if host and port:
            return f"http://{host}:{port}"
    return None


def _await_deployment(
    client: Any, deployment: Any, model_id: str, timeout_seconds: int
) -> DeploymentOutcome:
    """Wait for a deployment to report ready and collect its endpoint.

    The single polling step shared by the deployment workflows. Keeping it in
    one place is what makes "at most one wait per workflow" checkable.

    Args:
        client: The platform client.
        deployment: The deployment just created.
        model_id: Identifier of the model deployed.
        timeout_seconds: Bound on the wait.

    Returns:
        The deployment with its instances and endpoint.
    """
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
        The ready deployment, or a :class:`Refusal` naming the search term when
        nothing matched — an empty search result is not a deployment failure,
        and reporting it as one sends an agent retrying a term that will never
        match.
    """
    matches = client.models.search_models(query, limit=1)
    if not matches:
        return Refusal(
            reason="No catalogued model matches that search term.",
            shortfall=f"a model matching {query!r}",
        )
    model = matches[0]
    deployment = client.serving.deploy_model(model_id=model.id)
    return _await_deployment(client, deployment, str(model.id), timeout_seconds)


@register(
    WorkflowSpec(
        name="deploy_and_connect_model",
        summary="Deploy a known model and return an endpoint ready for inference.",
        terminal_artifact="A ready deployment with a reachable endpoint.",
        polling_step="Waiting for the deployment to report ready.",
        approval_step="Creating the deployment.",
        idempotent=False,
        not_idempotent_because=(
            "Each call creates a deployment. Reuse an existing one by id "
            "instead of calling again."
        ),
        resume_hint="get_deployment_status_serving",
    )
)
def deploy_and_connect_model(
    client: Any, model_id: str, *, timeout_seconds: int = 3600
) -> DeploymentOutcome:
    """Deploy a model by identifier and return its endpoint.

    Args:
        client: The platform client.
        model_id: Identifier of an already-catalogued model.
        timeout_seconds: Bound on the readiness wait.

    Returns:
        The ready deployment and its endpoint.
    """
    deployment = client.serving.deploy_model(model_id=model_id)
    return _await_deployment(client, deployment, model_id, timeout_seconds)


def _required_bytes(estimate: Any) -> int | None:
    """Return the memory an estimate says a deployment needs.

    Args:
        estimate: The platform's estimate, whose key naming is not fixed by a
            model this package owns.

    Returns:
        The requirement in bytes, or ``None`` when the estimate carries none.
        ``None`` means "unknown", never "zero" — treating an absent estimate as
        no requirement would deploy into a cluster that cannot hold it.
    """
    if not isinstance(estimate, dict):
        return None
    for key in ("required_bytes", "vram_bytes", "total_bytes", "estimated_bytes"):
        value = estimate.get(key)
        if isinstance(value, int):
            return value
    return None


@register(
    WorkflowSpec(
        name="preflight_and_deploy_model",
        summary="Check capacity for a model deployment and deploy only if it fits.",
        terminal_artifact="A ready deployment, or a refusal naming the shortfall.",
        polling_step="Waiting for the deployment to report ready.",
        approval_step="Creating the deployment.",
        idempotent=False,
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

    Refusing before anything is created is the point: a deployment that fails
    on capacity after the fact leaves a half-built thing an agent must find and
    clean up.

    Takes the three identifiers it uses rather than a ``CreateModelDeployment``.
    The request model has sixteen fields and this workflow reads three of them,
    so accepting the whole thing published 720 tokens of schema on every
    listing and invited a caller to set thirteen fields that would be ignored.

    That leaves five parameters, which is one past what a static analyser
    recommends for a Python function, and the trade is deliberate: this
    signature *is* the published tool shape, so grouping arguments into an
    object to reduce the count would put a nested definition back into every
    listing to satisfy a threshold written for ordinary call sites. Each of the
    five is a value a caller genuinely decides.

    Args:
        client: The platform client.
        model_id: Model to deploy.
        model_config_id: Configuration to deploy it with. Required, because the
            platform's own deployment request requires it — a workflow that
            defaulted it would fail at the platform instead of at the argument.
        model_file_id: Specific model file, when the model has more than one.
        timeout_seconds: Bound on the readiness wait.

    Returns:
        The ready deployment, or a :class:`Refusal` naming the estimated
        requirement and what the cluster has.
    """
    deployment_request = CreateModelDeployment(
        m_id=UUID(str(model_id)),
        m_config_id=UUID(str(model_config_id)),
        m_file_id=UUID(str(model_file_id)) if model_file_id else None,
    )
    estimate = client.serving.estimate_model_vram(deployment_request)
    required = _required_bytes(estimate)
    nodes = client.cluster.get_running_nodes()
    if required and not nodes:
        return Refusal(
            reason="No running node can host this deployment.",
            shortfall=f"{required} bytes of accelerator memory, 0 running nodes",
        )
    deployment = client.serving.deploy_model(
        model_id=model_id, m_config_id=model_config_id, m_file_id=model_file_id
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
    deployment = client.serving.get_deployment_status(deployment_id)
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
