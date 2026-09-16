"""Workroom and garden-app workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._contract import Refusal, WorkflowSpec, register

__all__ = [
    "AppRequest",
    "WorkroomDraft",
    "create_workroom_and_enter",
    "deploy_app_from_garden",
    "export_workroom_bundle",
]


@dataclass(frozen=True, slots=True)
class WorkroomDraft:
    """What a new workroom is.

    Attributes:
        name: Workroom name.
        workroom_type: Workroom type.
        description: Optional description.
        classification: Optional classification.
    """

    name: str
    workroom_type: str
    description: str | None = None
    classification: str | None = None


@register(
    WorkflowSpec(
        name="create_workroom_and_enter",
        summary="Create a workroom and enter it, returning its scope context.",
        terminal_artifact="The workroom id and the scope context for later calls.",
        polling_step=None,
        approval_step="Creating the workroom.",
        idempotent=False,
        not_idempotent_because=(
            "Each call creates another workroom with the same name. Call "
            "list_workrooms first to find an existing one."
        ),
    )
)
def create_workroom_and_enter(client: Any, draft: WorkroomDraft) -> dict[str, Any]:
    """Create a workroom and enter it in one call.

    Args:
        client: The platform client.
        draft: What the workroom should be.

    Returns:
        Mapping with ``workroom`` and the ``context`` entering returned, which
        later calls need to stay inside the workroom's scope.
    """
    workroom = client.workrooms.create(
        draft.name,
        draft.workroom_type,
        description=draft.description,
        classification=draft.classification,
    )
    workroom_id = getattr(workroom, "id", workroom)
    return {
        "workroom": str(workroom_id),
        "context": client.workrooms.enter(workroom_id),
    }


@register(
    WorkflowSpec(
        name="export_workroom_bundle",
        summary="Export a workroom bundle and return its location and manifest.",
        terminal_artifact="The bundle's location and its manifest.",
        polling_step=None,
        approval_step="Exporting the bundle.",
        idempotent=True,
    )
)
def export_workroom_bundle(
    client: Any, workroom_id: str, *, output_path: str | None = None
) -> dict[str, Any]:
    """Export a workroom's bundle and return where it went with its manifest.

    The manifest travels with the location because a bundle whose contents
    cannot be verified is a file, not an export.

    Args:
        client: The platform client.
        workroom_id: Workroom to export.
        output_path: Where to write the bundle. Streams to a path rather than
            returning bytes, since a bundle can be large enough that carrying
            it through an agent's context is the wrong shape.

    Returns:
        Mapping with ``location`` and ``manifest``.
    """
    location = client.workrooms.export_bundle(workroom_id, output_path=output_path)
    return {
        "location": str(location),
        "manifest": client.workrooms.get_export_manifest(workroom_id),
    }


@dataclass(frozen=True, slots=True)
class AppRequest:
    """Which garden app to deploy, and where.

    Attributes:
        name: App name in the garden.
        version: Specific version, or the latest when omitted.
        deployment_name: Name for the deployment.
        workroom_id: Workroom to deploy into.
    """

    name: str
    version: str | None = None
    deployment_name: str | None = None
    workroom_id: str | None = None


@register(
    WorkflowSpec(
        name="deploy_app_from_garden",
        summary="Install an app from the garden and deploy it, returning its status.",
        terminal_artifact="The app deployment id and its status.",
        polling_step="Waiting for the app deployment to report a terminal status.",
        approval_step="Deploying the app.",
        idempotent=False,
        not_idempotent_because=(
            "Each call creates another deployment. Call list_deployments_apps "
            "first to find an existing one."
        ),
        resume_hint="get_deployment_status_apps",
    )
)
def deploy_app_from_garden(
    client: Any, request: AppRequest
) -> dict[str, Any] | Refusal:
    """Install an app by name from the garden and deploy it.

    Args:
        client: The platform client.
        request: Which app to deploy and where.

    Returns:
        Mapping with ``deployment`` and ``status``, or a :class:`Refusal` when
        the garden has no app by that name — which is not a deployment failure
        and must not be reported as one.
    """
    template = client.apps.find_template(request.name, request.version)
    if template is None:
        return Refusal(
            reason="The garden has no app template with that name.",
            shortfall=f"an app template named {request.name!r}",
        )
    deployment = client.apps.install_by_name(
        request.name,
        version=request.version,
        deployment_name=request.deployment_name,
        workroom_id=request.workroom_id,
    )
    deployment_id = getattr(deployment, "id", deployment)
    return {
        "deployment": str(deployment_id),
        "status": client.apps.get_deployment_status(deployment_id),
    }
