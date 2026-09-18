"""Workroom and garden-app workflows."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
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
    """

    name: str
    workroom_type: str
    description: str | None = None


@register(
    WorkflowSpec(
        name="create_workroom_and_enter",
        summary="Create a workroom and enter it, returning its scope context.",
        terminal_artifact="The workroom id and the scope context for later calls.",
        polling_step=None,
        approval_step="Creating the workroom.",
        idempotent=False,
        reads_only=False,
        destructive=False,
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
    )
    workroom_id = getattr(workroom, "id", workroom)
    return {
        "workroom": str(workroom_id),
        "context": client.workrooms.enter(workroom_id),
    }


#: Where a host lets an agent-triggered export land. A host sets this; an agent
#: cannot. Unset means the host approved no directory, and the workflow refuses
#: rather than choosing one, so an export never writes somewhere a host did not
#: name. Overwriting a file already inside the directory is allowed, because a
#: second export of the same workroom has to rewrite its own bundle for the
#: ``idempotent`` declaration above to hold.
_EXPORT_DIRECTORY_ENV = "KAMIWAZA_AGENT_EXPORT_DIR"


def _bundle_destination(workroom_id: str, output_path: str | None) -> Path | Refusal:
    """Resolve where a bundle may be written, or refuse to write one.

    Args:
        workroom_id: Workroom being exported, which names the default file.
        output_path: File name the caller asked for, or ``None``.

    Returns:
        An absolute destination inside the configured directory, or a
        ``Refusal`` when no directory is configured or the request leaves it.
    """
    # Refusing beats falling back to no path. With no path
    # WorkroomService.export_bundle returns ``response.content``, and that ZIP
    # reaches the agent as a bytes repr: a 44-byte archive measured 173
    # characters, and a real bundle is megabytes.
    configured = os.environ.get(_EXPORT_DIRECTORY_ENV, "").strip()
    if not configured:
        return Refusal(
            reason="This host configured no directory for workroom exports.",
            shortfall=(
                f"{_EXPORT_DIRECTORY_ENV} is unset, so no approved directory "
                f"exists to write a bundle into."
            ),
        )
    allowed = Path(configured).expanduser().resolve()
    requested = output_path or f"workroom-{workroom_id}-bundle.zip"
    # resolve() collapses "..", follows symlinks, and a joined absolute path
    # discards the left side, so traversal, an absolute path and a symlink
    # pointing out of the directory all fail this one containment check.
    destination = (allowed / requested).resolve()
    if destination == allowed or not destination.is_relative_to(allowed):
        return Refusal(
            reason="A workroom bundle is written only inside this host's export directory.",
            shortfall=f"{requested} resolves to {destination}, outside {allowed}.",
        )
    return destination


@register(
    WorkflowSpec(
        name="export_workroom_bundle",
        summary="Export a workroom bundle and return its location and manifest.",
        terminal_artifact="The bundle's location and its manifest.",
        polling_step=None,
        approval_step="Writing the bundle into the host's export directory.",
        idempotent=False,
        not_idempotent_because=(
            "Each call emits an export audit event on the platform and "
            "rewrites the destination file, so a retry is not the same act."
        ),
        reads_only=False,
        destructive=False,
    )
)
def export_workroom_bundle(
    client: Any, workroom_id: str, *, output_path: str | None = None
) -> dict[str, Any] | Refusal:
    """Export a workroom's bundle into the host's export directory.

    Args:
        client: The platform client.
        workroom_id: Workroom to export.
        output_path: File name under the host's export directory; a path
            outside it is refused.

    Returns:
        Mapping with ``location``, ``size_bytes`` and ``manifest``, or a
        ``Refusal``.
    """
    # The manifest travels with the location because a bundle whose contents
    # cannot be verified is a file, not an export. The default file name and
    # the KAMIWAZA_AGENT_EXPORT_DIR variable are named here rather than in the
    # docstring because every listing pays for published text: measured with
    # cl100k_base, this workflow's summary, terminal artifact, approval
    # sentence, non-idempotence reason and docstring come to 140 tokens
    # against the 143 the pre-fix four cost. A refusal names the variable
    # where it matters, and the default name is workroom-<id>-bundle.zip.
    #
    # The audit event in the reason above is emitted per call by the platform
    # handler: kamiwaza/kamiwaza/services/workrooms/api.py:3938, event
    # "kamiwaza.audit.sensitive_data.export".
    destination = _bundle_destination(workroom_id, output_path)
    if isinstance(destination, Refusal):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    # The service's return value is dropped on purpose. Given a path it is that
    # path, and given none it is the ZIP itself, so publishing the destination
    # this function resolved leaves no branch that can carry bytes outward.
    client.workrooms.export_bundle(workroom_id, output_path=str(destination))
    return {
        "location": str(destination),
        "size_bytes": destination.stat().st_size,
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
        summary="Install an app from the garden and deploy it, returning its starting status.",
        terminal_artifact="The app deployment id and the status it started in.",
        polling_step=None,
        approval_step="Deploying the app.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Each call creates another deployment. Call list_deployments_apps "
            "first to find an existing one."
        ),
    )
)
def deploy_app_from_garden(
    client: Any, request: AppRequest
) -> dict[str, Any] | Refusal:
    """Install an app by name from the garden and deploy it.

    Nothing waits: the status is read once after the install, so it is the one
    the deployment starts in, not a settled one. Follow with
    get_deployment_status_apps.

    Args:
        client: The platform client.
        request: Which app to deploy and where.

    Returns:
        Mapping with ``deployment`` and the ``status`` the deployment reported
        immediately after install, or a :class:`Refusal` when the garden has
        no app by that name — which is not a deployment failure and must not
        be reported as one.
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
