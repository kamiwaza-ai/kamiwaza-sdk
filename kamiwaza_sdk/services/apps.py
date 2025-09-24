# kamiwaza_sdk/services/apps.py

from typing import List, Optional, Dict, Any
from uuid import UUID
import logging

from .base_service import BaseService
from ..schemas.apps import (
    AppTemplate,
    CreateAppDeployment,
    AppDeployment,
    AppInstance,
    ImageStatus,
    ImagePullResult,
    GardenApp
)
from ..exceptions import APIError, NotFoundError


class AppService(BaseService):
    """Service for managing containerized applications in the App Garden."""
    
    def __init__(self, client):
        super().__init__(client)
        self.logger = logging.getLogger(__name__)
    
    # Deployment Operations
    
    def deploy(
        self,
        template_id: UUID,
        name: str,
        env_vars: Optional[Dict[str, str]] = None,
        min_copies: int = 1,
        starting_copies: int = 1,
        max_copies: Optional[int] = None
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
            max_copies=max_copies
        )
        
        try:
            response = self.client.post(
                "/apps/deploy_app",
                json=deployment_request.model_dump()
            )
            return AppDeployment.model_validate(response)
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Template {template_id} not found")
            raise
    
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
    
    def list_templates(self) -> List[AppTemplate]:
        """
        List all available application templates.
        
        Returns:
            List of AppTemplate objects
        """
        response = self.client.get("/apps/app_templates")
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
    
    def list_garden_apps(self) -> List[GardenApp]:
        """
        List pre-built applications available in the Kamiwaza garden.
        
        Returns:
            List of GardenApp objects
        """
        response = self.client.get("/apps/kamiwaza_garden")
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