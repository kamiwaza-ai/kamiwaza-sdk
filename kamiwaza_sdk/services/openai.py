# kamiwaza_sdk/services/openai.py    

from typing import Optional
from uuid import UUID
import httpx
from openai import OpenAI
from .base_service import BaseService
from ..exceptions import APIError, AuthenticationError

class OpenAIService(BaseService):
    def get_client(
        self,
        model: Optional[str] = None,
        deployment_id: Optional[UUID] = None,
        repo_id: Optional[str] = None,
        endpoint: Optional[str] = None
    ) -> OpenAI:
        """
        Get an OpenAI client configured for a specific model deployment.
        
        Args:
            model (Optional[str]): The name of the deployed model.
            deployment_id (Optional[UUID]): The ID of the deployment.
            repo_id (Optional[str]): The Hugging Face repo ID of the model.
            endpoint (Optional[str]): Direct endpoint URL to use.
            
        Returns:
            OpenAI: Configured OpenAI client for the specified deployment.
            
        Note:
            You must specify exactly one of: model, deployment_id, repo_id, or endpoint.
        """
        if endpoint:
            base_url = endpoint
        else:
            deployments = self.client.serving.list_active_deployments()
            
            if deployment_id:
                deployment = next(
                    (d for d in deployments if str(d.id) == str(deployment_id)),
                    None
                )
            elif model:
                deployment = next(
                    (d for d in deployments if d.m_name == model),
                    None
                )
            elif repo_id:
                # First, get the model ID for the repo ID
                model_obj = self.client.models.get_model_by_repo_id(repo_id)
                if not model_obj:
                    raise ValueError(f"No model found with repo ID: {repo_id}")
                
                # Then find deployments for this model
                deployment = next(
                    (d for d in deployments if str(d.m_id) == str(model_obj.id)),
                    None
                )
            else:
                raise ValueError("Must specify either model, deployment_id, repo_id, or endpoint")
                
            if not deployment:
                identifier_type = 'model' if model else 'repo_id' if repo_id else 'deployment_id'
                identifier_value = model or repo_id or deployment_id
                raise ValueError(
                    f"No active deployment found for specified {identifier_type}: {identifier_value}"
                )
                
            base_url = deployment.endpoint

        # Retrieve bearer token from the authenticated client (PAT or session token)
        api_key = self.client.get_bearer_token()
        if not api_key:
            raise AuthenticationError(
                "Unable to configure OpenAI client without an authenticated session or API key."
            )

        # Create httpx client with same verify setting as Kamiwaza client
        http_client = httpx.Client(verify=self.client.session.verify)
        
        return OpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client
        )
