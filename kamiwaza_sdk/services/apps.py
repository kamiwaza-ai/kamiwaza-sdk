# kamiwaza_sdk/services/apps.py

from typing import List, Optional, Dict, Any
from uuid import UUID
import logging
import warnings

from packaging.version import InvalidVersion, Version

from .base_service import BaseService
from ..schemas.apps import (
    AppTemplate,
    CreateAppDeployment,
    AppDeployment,
    AppInstance,
    ImageStatus,
    ImagePullResult,
    GardenApp,
)
from ..exceptions import APIError, NotFoundError


_KAIZEN_TEMPLATE_NAMES = frozenset({"kaizen", "kaizen-next"})
_KAIZEN_V4_COMPOSE_MARKERS = (
    "images/kaizen-api:",
    "KAIZEN_PROCESS: worker",
    "PI_KAIZEN_CALLBACK_URL:",
)


def _kaizen_v4_version(template: AppTemplate) -> Optional[Version]:
    """Return a sortable version only for trusted Kaizen v4 templates."""
    try:
        parsed_version = Version(template.version or "")
    except InvalidVersion:
        return None
    if not parsed_version.release or parsed_version.release[0] != 4:
        return None
    if template.source_type.value != "kamiwaza":
        return None
    if template.visibility.value != "public":
        return None
    if not all(marker in template.compose_yml for marker in _KAIZEN_V4_COMPOSE_MARKERS):
        return None
    return parsed_version


class AppService(BaseService):
    """Service for managing containerized applications in the App Garden.

    .. deprecated::
        AppService is deprecated and will be removed in a future release.
        The Docker Compose-based App Garden is being replaced by Kubernetes
        CRD-based extensions. Use :class:`ExtensionService` instead::

            # Old (deprecated)
            client.apps.list_deployments()
            client.apps.deploy(template_id=..., name="my-app")
            client.apps.stop_deployment(deployment_id)

            # New (recommended)
            client.extensions.list_extensions()
            client.extensions.create_extension(request)
            client.extensions.delete_extension(name)
    """

    def __init__(self, client):
        super().__init__(client)
        self.logger = logging.getLogger(__name__)
        warnings.warn(
            "AppService is deprecated. Use ExtensionService instead. "
            "See https://docs.kamiwaza.ai for migration guidance.",
            DeprecationWarning,
            stacklevel=2,
        )

    # Deployment Operations

    def deploy(
        self,
        template_id: UUID,
        name: str,
        env_vars: Optional[Dict[str, str]] = None,
        min_copies: int = 1,
        starting_copies: int = 1,
        max_copies: Optional[int] = None,
        workroom_id: Optional[str] = None,
    ) -> AppDeployment:
        """
        Deploy a new application from a template.

        Args:
            template_id: UUID of the template to deploy
            name: Name for the deployment
            env_vars: Optional environment variables
            min_copies: Minimum number of instances
            starting_copies: Initial number of instances
            max_copies: Maximum number of instances (for autoscaling)
            workroom_id: Workroom to deploy into. The platform derives the
                deployment's workroom from the caller's resolved context, so
                this is sent as the ``X-Workroom-Id`` header rather than a body
                field.

        Returns:
            AppDeployment object with deployment details

        Raises:
            APIError: If deployment fails
            NotFoundError: If template not found
        """
        deployment_request = CreateAppDeployment(
            name=name,
            template_id=template_id,
            env_vars=env_vars or {},
            min_copies=min_copies,
            starting_copies=starting_copies,
            max_copies=max_copies,
        )

        headers = {"X-Workroom-Id": str(workroom_id)} if workroom_id else None
        try:
            response = self.client.post(
                "/apps/deploy_app",
                # mode="json" so template_id (UUID) serializes to a string;
                # the requests JSON encoder can't adapt a raw UUID.
                json=deployment_request.model_dump(mode="json"),
                headers=headers,
            )
            return AppDeployment.model_validate(response)
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Template {template_id} not found")
            raise

    def find_template(
        self, name: str, version: Optional[str] = None
    ) -> Optional[AppTemplate]:
        """Find a catalog template by name (and optional version).

        Args:
            name: Template name (exact match).
            version: Optional version to disambiguate when multiple revisions
                of the same name exist.

        Returns:
            The matching AppTemplate, or None if no template matches.
        """
        templates = self.list_templates()
        if name.lower() == "kaizen":
            candidates: List[tuple[Version, bool, AppTemplate]] = []
            for template in templates:
                if template.name.lower() not in _KAIZEN_TEMPLATE_NAMES:
                    continue
                parsed_version = _kaizen_v4_version(template)
                if parsed_version is None:
                    continue
                if version is not None and template.version != version:
                    continue
                candidates.append((parsed_version, template.name == "kaizen", template))
            if not candidates:
                return None
            return max(candidates, key=lambda candidate: candidate[:2])[2]

        for template in templates:
            if template.name != name:
                continue
            if version is not None and template.version != version:
                continue
            return template
        return None

    def install_by_name(
        self,
        name: str,
        *,
        version: Optional[str] = None,
        deployment_name: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None,
        workroom_id: Optional[str] = None,
        min_copies: int = 1,
        starting_copies: int = 1,
        max_copies: Optional[int] = None,
        sync_if_missing: bool = True,
    ) -> AppDeployment:
        """Install a catalog extension by name, resolving its template id.

        This is the current install-by-name path: it resolves the named
        catalog template and deploys it via the App Garden, so callers don't
        hand-author a full extension CR. When the template isn't in the local
        catalog yet and ``sync_if_missing`` is set, it imports the garden
        catalog once and retries the lookup.

        Args:
            name: Catalog template/extension name (e.g. "kaizen").
            version: Optional version to pin.
            deployment_name: Name for the deployment (defaults to ``name``).
            env_vars: Optional environment variables for the deployment.
            workroom_id: Workroom to install into (X-Workroom-Id header).
            min_copies / starting_copies / max_copies: Scaling parameters.
            sync_if_missing: Import the garden catalog and retry if the named
                template isn't found locally.

        Returns:
            AppDeployment for the installed extension.

        Raises:
            NotFoundError: If no template matches after an optional sync.
        """
        template = self.find_template(name, version)
        if template is None and sync_if_missing:
            self.import_garden_apps()
            template = self.find_template(name, version)
        if template is None:
            suffix = f" (version {version})" if version else ""
            raise NotFoundError(f"No catalog template named '{name}'{suffix} found")

        return self.deploy(
            template_id=template.id,
            name=deployment_name or name,
            env_vars=env_vars,
            min_copies=min_copies,
            starting_copies=starting_copies,
            max_copies=max_copies,
            workroom_id=workroom_id,
        )

    def list_deployments(self) -> List[AppDeployment]:
        """
        List all application deployments.

        Returns:
            List of AppDeployment objects
        """
        response = self.client.get("/apps/deployments")
        return [AppDeployment.model_validate(item) for item in response]

    def get_deployment(self, deployment_id: UUID) -> AppDeployment:
        """
        Get details of a specific deployment.

        Args:
            deployment_id: UUID of the deployment

        Returns:
            AppDeployment object

        Raises:
            NotFoundError: If deployment not found
        """
        try:
            response = self.client.get(f"/apps/deployment/{deployment_id}")
            return AppDeployment.model_validate(response)
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Deployment {deployment_id} not found")
            raise

    def get_deployment_status(self, deployment_id: UUID) -> str:
        """
        Get the current status of a deployment.

        Args:
            deployment_id: UUID of the deployment

        Returns:
            Status string (e.g., "RUNNING", "STOPPED", "FAILED")

        Raises:
            NotFoundError: If deployment not found
        """
        try:
            response = self.client.get(f"/apps/deployment/{deployment_id}/status")
            return response
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Deployment {deployment_id} not found")
            raise

    def stop_deployment(self, deployment_id: UUID) -> bool:
        """
        Stop an application deployment.

        Args:
            deployment_id: UUID of the deployment to stop

        Returns:
            True if successfully stopped

        Raises:
            APIError: If stop operation fails
        """
        try:
            self.client.delete(f"/apps/deployment/{deployment_id}")
            return True
        except APIError as e:
            self.logger.error(f"Failed to stop deployment {deployment_id}: {e}")
            raise

    def list_instances(self, deployment_id: Optional[UUID] = None) -> List[AppInstance]:
        """
        List application instances, optionally filtered by deployment.

        Args:
            deployment_id: Optional deployment ID to filter by

        Returns:
            List of AppInstance objects
        """
        params = {}
        if deployment_id:
            params["deployment_id"] = str(deployment_id)

        response = self.client.get("/apps/instances", params=params)
        return [AppInstance.model_validate(item) for item in response]

    def get_instance(self, instance_id: UUID) -> AppInstance:
        """
        Get details of a specific instance.

        Args:
            instance_id: UUID of the instance

        Returns:
            AppInstance object

        Raises:
            NotFoundError: If instance not found
        """
        try:
            response = self.client.get(f"/apps/instance/{instance_id}")
            return AppInstance.model_validate(response)
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Instance {instance_id} not found")
            raise

    # Template Operations

    def list_templates(self, template_type: Optional[str] = None) -> List[AppTemplate]:
        """
        List available application templates.

        Args:
            template_type: Optional filter for template type. Valid values are
                "app", "service", or "tool". If omitted, all app templates are
                returned.

        Returns:
            List of AppTemplate objects
        """
        params = {"template_type": template_type} if template_type else None
        response = self.client.get("/apps/app_templates", params=params)
        return [AppTemplate.model_validate(item) for item in response]

    def get_template(self, template_id: UUID) -> AppTemplate:
        """
        Get details of a specific template.

        Args:
            template_id: UUID of the template

        Returns:
            AppTemplate object

        Raises:
            NotFoundError: If template not found
        """
        try:
            response = self.client.get(f"/apps/app_templates/{template_id}")
            return AppTemplate.model_validate(response)
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Template {template_id} not found")
            raise

    def delete_template(self, template_id: UUID) -> dict:
        """
        Delete an application template by ID.

        Args:
            template_id: UUID of the template

        Returns:
            Deletion result payload from the API.
        """
        try:
            return self.client.delete(f"/apps/app_templates/{template_id}")
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Template {template_id} not found")
            raise

    def list_garden_apps(self, *, force_refresh: bool = False) -> List[GardenApp]:
        """
        List pre-built applications available in the Kamiwaza garden.

        Note: This is backed by the remote catalog endpoint (`GET /apps/remote/apps`).
        
        Returns:
            List of GardenApp objects
        """
        params = {"force_refresh": "true"} if force_refresh else None
        response = self.client.get("/apps/remote/apps", params=params)
        return [GardenApp.model_validate(item) for item in response]

    def import_garden_apps(self) -> Dict[str, Any]:
        """
        Import missing garden apps as templates.

        Note: This requires appropriate permissions.

        Returns:
            Dictionary with import results including:
            - imported_count: Number of apps imported
            - total_apps: Total number of garden apps
            - errors: List of any errors encountered
            - success: Whether all imports succeeded
        """
        response = self.client.post("/apps/garden/import")
        return response

    # Image Management

    def check_image_status(self, template_id: UUID) -> ImageStatus:
        """
        Check if Docker images for a template have been pulled.

        Args:
            template_id: UUID of the template

        Returns:
            ImageStatus object with pull status for each image

        Raises:
            NotFoundError: If template not found
        """
        try:
            response = self.client.get(f"/apps/images/status/{template_id}")
            return ImageStatus.model_validate(response)
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Template {template_id} not found")
            raise

    def pull_images(self, template_id: UUID) -> ImagePullResult:
        """
        Pull all Docker images required by a template.

        This should be done before deploying an app for the first time
        to ensure images are available locally.

        Args:
            template_id: UUID of the template

        Returns:
            ImagePullResult object with pull results

        Raises:
            NotFoundError: If template not found
            APIError: If pull operation fails
        """
        try:
            response = self.client.post(f"/apps/images/pull/{template_id}")
            return ImagePullResult.model_validate(response)
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Template {template_id} not found")
            raise
