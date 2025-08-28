# kamiwaza_sdk/services/tools.py

from typing import List, Optional, Dict, Any
from uuid import UUID
import logging

from .base_service import BaseService
from ..schemas.tools import (
    CreateToolDeployment,
    ToolDeployment,
    ToolServerInfo,
    ToolDiscovery,
    ToolHealthCheck,
    DeployFromTemplateRequest,
    ToolTemplate
)
from ..exceptions import APIError, NotFoundError


class ToolService(BaseService):
    """Service for managing Tool servers (MCP - Model Context Protocol)."""
    
    def __init__(self, client):
        super().__init__(client)
        self.logger = logging.getLogger(__name__)
    
    # Deployment Operations
    
    def deploy(
        self,
        name: str,
        image: str,
        env_vars: Optional[Dict[str, str]] = None,
        min_copies: int = 1,
        max_copies: int = 1
    ) -> ToolDeployment:
        """
        Deploy a new Tool server from a Docker image.
        
        This creates a Tool deployment and returns a public URL that can be used
        with any MCP-compatible client.
        
        Args:
            name: Name for the deployment
            image: Docker image for the Tool server
            env_vars: Optional environment variables
            min_copies: Minimum number of instances
            max_copies: Maximum number of instances
            
        Returns:
            ToolDeployment with the generated public URL
            
        Raises:
            APIError: If deployment fails
        """
        deployment_request = CreateToolDeployment(
            name=name,
            image=image,
            env_vars=env_vars or {},
            min_copies=min_copies,
            max_copies=max_copies
        )
        
        response = self.client.post(
            "/tool/deploy",
            json=deployment_request.model_dump()
        )
        return ToolDeployment.model_validate(response)
    
    def deploy_from_template(
        self,
        template_name: str,
        name: str,
        env_vars: Optional[Dict[str, str]] = None
    ) -> ToolDeployment:
        """
        Deploy a Tool server from a pre-built template.
        
        This is a convenience method that combines template lookup and deployment.
        
        Args:
            template_name: Name of the template (e.g., "tool-websearch")
            name: Name for your deployment instance
            env_vars: Optional environment variables (e.g., API keys)
            
        Returns:
            ToolDeployment with the generated public URL
            
        Raises:
            NotFoundError: If template not found
            APIError: If required environment variables are missing
            
        Example:
            >>> deployment = client.tools.deploy_from_template(
            ...     template_name="tool-websearch",
            ...     name="my-search-tool",
            ...     env_vars={"TAVILY_API_KEY": "your-api-key"}
            ... )
            >>> print(f"Tool URL: {deployment.url}")
        """
        request = DeployFromTemplateRequest(
            name=name,
            env_vars=env_vars
        )
        
        try:
            response = self.client.post(
                f"/tool/deploy-template/{template_name}",
                json=request.model_dump()
            )
            return ToolDeployment.model_validate(response)
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Template {template_name} not found")
            elif "400" in str(e) and "Missing required environment variables" in str(e):
                # Parse the error to provide clearer message
                raise APIError(str(e))
            raise
    
    def list_deployments(self) -> List[ToolDeployment]:
        """
        List all Tool deployments.
        
        Returns a list of all active Tool server deployments with their public URLs.
        
        Returns:
            List of ToolDeployment objects
        """
        response = self.client.get("/tool/deployments")
        return [ToolDeployment.model_validate(item) for item in response]
    
    def get_deployment(self, deployment_id: UUID) -> ToolDeployment:
        """
        Get details of a specific Tool deployment.
        
        Args:
            deployment_id: UUID of the deployment
            
        Returns:
            ToolDeployment with URL
            
        Raises:
            NotFoundError: If deployment not found
        """
        try:
            response = self.client.get(f"/tool/deployment/{deployment_id}")
            return ToolDeployment.model_validate(response)
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Tool deployment {deployment_id} not found")
            raise
    
    def stop_deployment(self, deployment_id: UUID) -> bool:
        """
        Stop and remove a Tool deployment.
        
        This will terminate the Tool server container(s) and clean up resources.
        
        Args:
            deployment_id: UUID of the deployment to stop
            
        Returns:
            True if successfully stopped
            
        Raises:
            NotFoundError: If deployment not found
            APIError: If stop operation fails
        """
        try:
            self.client.delete(f"/tool/deployment/{deployment_id}")
            return True
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Tool deployment {deployment_id} not found")
            raise
    
    # Discovery and Health
    
    def discover_servers(self) -> ToolDiscovery:
        """
        Discover available Tool servers and their capabilities.
        
        This returns information about all deployed Tool servers,
        including their capabilities when available.
        
        Returns:
            ToolDiscovery object with list of available servers
        """
        response = self.client.get("/tool/discover")
        return ToolDiscovery.model_validate(response)
    
    def check_health(self, deployment_id: UUID) -> ToolHealthCheck:
        """
        Check the health status of a Tool deployment.
        
        This performs a health check on the specified Tool server
        to verify it's running and responding to the MCP protocol.
        
        Args:
            deployment_id: UUID of the deployment to check
            
        Returns:
            ToolHealthCheck with status information
            
        Raises:
            NotFoundError: If deployment not found
        """
        try:
            response = self.client.get(f"/tool/deployment/{deployment_id}/health")
            return ToolHealthCheck.model_validate(response)
        except APIError as e:
            if "404" in str(e):
                raise NotFoundError(f"Tool deployment {deployment_id} not found")
            raise
    
    # Template Operations
    
    def list_available_templates(self) -> List[ToolTemplate]:
        """
        List available pre-built Tool templates.
        
        Returns a list of templates that can be deployed using deploy_from_template().
        
        Returns:
            List of ToolTemplate objects
        """
        response = self.client.get("/tool/templates/available")
        return [ToolTemplate.model_validate(item) for item in response]
    
    def list_imported_templates(self) -> List[ToolTemplate]:
        """
        List Tool templates that have been imported into the database.
        
        Returns only templates that have been imported and are ready to deploy.
        
        Returns:
            List of ToolTemplate objects with database IDs
        """
        response = self.client.get("/tool/templates")
        return [ToolTemplate.model_validate(item) for item in response]
    
    def get_garden_status(self) -> Dict[str, Any]:
        """
        Get status of Tool garden servers - available vs imported.
        
        Returns:
            Dictionary with:
            - tool_servers_available: Whether Tool servers file exists
            - total_tool_servers: Total number of available servers
            - imported_tool_servers: Number already imported
            - missing_tool_servers: List of servers not yet imported
        """
        response = self.client.get("/tool/garden/status")
        return response
    
    def import_garden_servers(self) -> Dict[str, Any]:
        """
        Import missing Tool servers from garden as templates.
        
        Note: This requires appropriate permissions.
        
        Returns:
            Dictionary with import results including:
            - imported_count: Number of servers imported
            - total_servers: Total number of Tool servers
            - errors: List of any errors encountered
            - success: Whether all imports succeeded
        """
        response = self.client.post("/tool/garden/import")
        return response