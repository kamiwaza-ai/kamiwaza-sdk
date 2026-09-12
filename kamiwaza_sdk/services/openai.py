# kamiwaza_sdk/services/openai.py    

import time
from typing import Optional
from uuid import UUID
import httpx
from openai import OpenAI
from .base_service import BaseService
from ..exceptions import APIError, AuthenticationError

class OpenAIService(BaseService):
    _ACTIVE_DEPLOYMENT_RECOVERY_ATTEMPTS = 12
    _ACTIVE_DEPLOYMENT_RECOVERY_DELAY_SECONDS = 1.0
    _NATIVE_HEALTH_RECOVERY_CODES = frozenset({
        "NATIVE_CPU_HEALTH_UNVERIFIED",
        "NATIVE_GPU_HEALTH_UNVERIFIED",
    })

    @classmethod
    def _is_native_recovery_candidate(cls, deployment: object) -> bool:
        status = str(getattr(deployment, "status", "")).upper()
        if status == "INITIALIZING":
            return True
        return (
            status in {"ERROR", "FAILED"}
            and getattr(deployment, "last_error_code", None)
            in cls._NATIVE_HEALTH_RECOVERY_CODES
        )

    def _find_active_deployment(self, deployment_id: UUID, initial_active):
        """Allow the server's bounded native Pod recovery window to settle."""
        current = self.client.serving.get_deployment(deployment_id)
        if not self._is_native_recovery_candidate(current):
            return None
        active = initial_active
        for attempt in range(self._ACTIVE_DEPLOYMENT_RECOVERY_ATTEMPTS):
            deployment = next(
                (item for item in active if str(item.id) == str(deployment_id)),
                None,
            )
            if deployment is not None:
                return deployment
            if attempt + 1 == self._ACTIVE_DEPLOYMENT_RECOVERY_ATTEMPTS:
                break
            time.sleep(self._ACTIVE_DEPLOYMENT_RECOVERY_DELAY_SECONDS)
            active = self.client.serving.list_active_deployments()
        return None

    def _resolve_deployment(
        self, deployments, *, model, deployment_id, repo_id
    ):
        if deployment_id:
            deployment = next(
                (item for item in deployments if str(item.id) == str(deployment_id)),
                None,
            )
            if deployment is None:
                try:
                    deployment = self._find_active_deployment(
                        deployment_id, deployments
                    )
                except APIError:
                    return None
            return deployment
        if model:
            return next(
                (item for item in deployments if item.m_name == model),
                None,
            )
        if repo_id:
            model_obj = self.client.models.get_model_by_repo_id(repo_id)
            if not model_obj:
                raise ValueError(f"No model found with repo ID: {repo_id}")
            return next(
                (item for item in deployments if str(item.m_id) == str(model_obj.id)),
                None,
            )
        raise ValueError("Must specify either model, deployment_id, repo_id, or endpoint")

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
            deployment = self._resolve_deployment(
                deployments,
                model=model,
                deployment_id=deployment_id,
                repo_id=repo_id,
            )
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
