# kamiwaza_client/services/openai.py    

from typing import Optional
from uuid import UUID
import httpx
from openai import OpenAI
from .base_service import BaseService
from ..exceptions import APIError

class OpenAIService(BaseService):
    def get_client(
        self,
        model: Optional[str] = None,
        deployment_id: Optional[UUID] = None,
        endpoint: Optional[str] = None
    ) -> OpenAI:
        """
        Get an OpenAI client configured for a specific model deployment.
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
            else:
                raise ValueError("Must specify either model, deployment_id, or endpoint")
                
            if not deployment:
                raise ValueError(
                    f"No active deployment found for specified {'model' if model else 'deployment_id'}"
                )
                
            base_url = deployment.endpoint

        # Create httpx client with same verify setting as Kamiwaza client
        http_client = httpx.Client(verify=self.client.session.verify)
        
        return OpenAI(
            api_key="local",
            base_url=base_url,
            http_client=http_client
        )