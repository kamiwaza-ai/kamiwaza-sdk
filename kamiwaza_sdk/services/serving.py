# kamiwaza_sdk/services/serving.py

import os
import time
from typing import Callable, Iterable, Iterator, List, Optional, Union
from uuid import UUID
from urllib.parse import urlparse

from ..schemas.serving.serving import (
    ActiveModelDeployment,
    ContainerLogListResponse,
    ContainerLogPatternResponse,
    ContainerLogResponse,
    CreateModelDeployment,
    ModelDeployment,
    ModelInstance,
    UIModelDeployment,
)
from ..schemas.serving.inference import (
    GenerateRequest,
    GenerateResponse,
    LoadModelRequest,
    LoadModelResponse,
    UnloadModelRequest,
    UnloadModelResponse,
)
from ..schemas.models.model_search import HubModelFileSearch
from .base_service import BaseService
from urllib.parse import urlparse
import os


class ServingService(BaseService):
    
    def start_ray(self, address: Optional[str] = None, runtime_env: Optional[dict] = None, options: Optional[dict] = None) -> None:
        """Start Ray with given parameters."""
        data = {
            "address": address,
            "runtime_env": runtime_env,
            "options": options
        }
        return self.client.post("/serving/start", json=data)

    def get_status(self) -> dict:
        """Get the status of Ray."""
        return self.client.get("/serving/status")

    def estimate_model_vram(self, deployment_request: CreateModelDeployment) -> dict:
        """Estimate the VRAM required for a model deployment."""
        return self.client.post("/serving/estimate_model_vram", json=deployment_request.model_dump())
    
    def deploy_model(self, 
                model_id: Optional[Union[str, UUID]] = None,
                repo_id: Optional[str] = None,
                m_config_id: Optional[Union[str, UUID]] = None,
                m_file_id: Optional[Union[str, UUID]] = None,
                **kwargs) -> Union[UUID, bool]:
        """
        Deploy a model based on the provided model ID or repo ID and optional parameters.

        Args:
            model_id (Optional[Union[str, UUID]]): The ID of the model to deploy.
                                                  Required if repo_id is not provided.
            repo_id (Optional[str]): The Hugging Face repo ID of the model to deploy.
                                    Required if model_id is not provided.
            m_config_id (Optional[Union[str, UUID]]): The ID of the model configuration to use.
            m_file_id (Optional[Union[str, UUID]]): The ID of the specific model file to use.
            **kwargs: Additional deployment parameters (engine_name, min_copies, etc.)

        Returns:
            Union[UUID, bool]: The deployment ID if successful, or False if deployment failed.
        """
        # Ensure at least one identifier is provided
        if model_id is None and repo_id is None:
            raise ValueError("Either model_id or repo_id must be provided")
            
        # If repo_id is provided but model_id isn't, look up the model_id
        if model_id is None and repo_id is not None:
            # Find the model with matching repo_id
            model = self.client.models.get_model_by_repo_id(repo_id)
            
            if not model:
                raise ValueError(f"No model found with repo ID: {repo_id}")
                
            model_id = model.id
        
        # Convert model_id to UUID if it's a string
        model_id = UUID(model_id) if isinstance(model_id, str) else model_id
        
        # If m_config_id is not provided, fetch the default configuration
        if m_config_id is None:
            configs = self.client.models.get_model_configs(model_id)
            if not configs:
                raise ValueError("No configurations found for this model")
            default_config = next((config for config in configs if config.default), configs[0])
            m_config_id = default_config.id

        # Prepare the deployment request
        deployment_request = CreateModelDeployment(
            m_id=model_id,
            m_config_id=m_config_id,
            m_file_id=m_file_id,
            **kwargs
        )

        # Convert UUIDs to strings in the deployment_request dictionary
        request_dict = deployment_request.model_dump()
        request_dict['m_id'] = str(request_dict['m_id'])
        if request_dict.get('m_file_id'):
            request_dict['m_file_id'] = str(request_dict['m_file_id'])
        request_dict['m_config_id'] = str(request_dict['m_config_id'])
    
        response = self.client.post("/serving/deploy_model", json=request_dict)
        return UUID(response) if isinstance(response, str) else response
    



    def list_active_deployments(self) -> List[ActiveModelDeployment]:
        deployments = self.list_deployments()
        active = []

        use_ssl = True
        if os.environ.get("KAMIWAZA_USE_HTTPS", "true").lower() == "false":
            use_ssl = False
        scheme = "https" if use_ssl else "http"

        # Parse the base URL to get host
        parsed_url = urlparse(self.client.base_url)
        host = parsed_url.netloc.split(':')[0]  # Remove port if present
        
        for deployment in deployments:
            running_instance = next(
                (i for i in deployment.instances if i.status == 'DEPLOYED'),
                None
            )
            
            if running_instance and deployment.status == 'DEPLOYED':
                access_path = deployment.access_path
                if access_path:
                    path = access_path if access_path.startswith("/") else f"/{access_path}"
                    path = path.rstrip("/")
                    if path.endswith("/v1"):
                        endpoint = f"{scheme}://{host}{path}"
                    else:
                        endpoint = f"{scheme}://{host}{path}/v1"
                elif deployment.lb_port and deployment.lb_port != 443:
                    endpoint = f"{scheme}://{host}:{deployment.lb_port}/v1"
                else:
                    endpoint = f"{scheme}://{host}/runtime/models/{deployment.id}/v1"
                
                active_deployment = ActiveModelDeployment(
                    id=deployment.id,
                    m_id=deployment.m_id,
                    m_name=deployment.m_name,
                    status=deployment.status,
                    instances=[i for i in deployment.instances if i.status == 'DEPLOYED'],
                    lb_port=deployment.lb_port,
                    endpoint=endpoint
                )
                active.append(active_deployment)

        return active


    def list_deployments(self, model_id: Optional[UUID] = None) -> List[UIModelDeployment]:
        """List all model deployments or filter by model_id."""
        params = {"model_id": str(model_id)} if model_id else None
        response = self.client.get("/serving/deployments", params=params)
        return [UIModelDeployment.model_validate(item) for item in response]

    def get_deployment(self, deployment_id: UUID) -> UIModelDeployment:
        """Get the details of a specific model deployment."""
        response = self.client.get(f"/serving/deployment/{deployment_id}")
        return UIModelDeployment.model_validate(response)

    def wait_for_deployment(
        self,
        deployment_id: Union[str, UUID],
        *,
        desired_status: Iterable[str] = ("DEPLOYED",),
        failure_status: Iterable[str] = ("FAILED", "ERROR"),
        poll_interval: float = 5.0,
        timeout: Optional[float] = 600.0,
    ) -> ModelDeployment:
        """Poll the deployment until it reaches a desired status."""

        poller = DeploymentStatusPoller(
            self,
            poll_interval=poll_interval,
            timeout=timeout,
        )
        return poller.wait_for(
            deployment_id,
            desired_status=desired_status,
            failure_status=failure_status,
        )

    def stop_deployment(self, 
                    deployment_id: Optional[UUID] = None, 
                    repo_id: Optional[str] = None,
                    force: Optional[bool] = False) -> bool:
        """
        Stop a model deployment.
        
        Args:
            deployment_id (Optional[UUID]): The ID of the deployment to stop.
                                          Required if repo_id is not provided.
            repo_id (Optional[str]): The Hugging Face repo ID of the model deployment to stop.
                                    Required if deployment_id is not provided.
            force (Optional[bool]): Whether to force stop the deployment. Defaults to False.
            
        Returns:
            bool: True if the deployment was successfully stopped, False otherwise.
        """
        # Ensure at least one identifier is provided
        if deployment_id is None and repo_id is None:
            raise ValueError("Either deployment_id or repo_id must be provided")
            
        # If repo_id is provided but deployment_id isn't, look up the deployment_id
        if deployment_id is None and repo_id is not None:
            # Get all deployments
            deployments = self.list_deployments()
            
            # Find the model ID for the repo ID
            model = self.client.models.get_model_by_repo_id(repo_id)
            if not model:
                raise ValueError(f"No model found with repo ID: {repo_id}")
                
            # Find deployments for this model
            matching_deployments = [d for d in deployments if str(d.m_id) == str(model.id)]
            # Filter out deployments that are not active
            matching_deployments = [d for d in matching_deployments if d.status == 'DEPLOYED']


            
            if not matching_deployments:
                raise ValueError(f"No active deployments found for model with repo ID: {repo_id}")
                
            if len(matching_deployments) > 1:
                # If multiple deployments exist, doesnt matter. Stop all.
                print(f"Multiple deployments found for {repo_id}. Stopping all of them.")
     
                for deployment in matching_deployments:
                    self.stop_deployment(deployment_id=deployment.id, force=force)
                return True
                
            deployment_id = matching_deployments[0].id
            
        # Convert deployment_id to UUID if it's a string
        deployment_id = UUID(deployment_id) if isinstance(deployment_id, str) else deployment_id
            
        return self.client.delete(f"/serving/deployment/{deployment_id}", params={"force": force})

    def get_deployment_status(self, deployment_id: UUID) -> ModelDeployment:
        """Get the status of a specific model deployment."""
        response = self.client.get(f"/serving/deployment/{deployment_id}/status")
        return ModelDeployment.model_validate(response)

    def get_deployment_logs(self, deployment_id: UUID) -> ContainerLogResponse:
        """Fetch captured logs for a deployment."""
        response = self.client.get(f"/serving/deployment/{deployment_id}/logs")
        return ContainerLogResponse.model_validate(response)

    def get_deployment_log_patterns(self, deployment_id: UUID) -> ContainerLogPatternResponse:
        """Fetch log pattern analysis for a deployment."""
        response = self.client.get(f"/serving/deployment/{deployment_id}/logs/patterns")
        return ContainerLogPatternResponse.model_validate(response)

    def list_engine_logs(self, engine_type: str) -> ContainerLogListResponse:
        """List available container logs for a given engine type."""
        response = self.client.get(f"/serving/logs/{engine_type}")
        return ContainerLogListResponse.model_validate(response)

    def stream_deployment_logs(
        self,
        deployment_id: Union[str, UUID],
        *,
        stop_when: Optional[Callable[[ContainerLogResponse], bool]] = None,
        poll_interval: float = 2.0,
        max_empty_polls: Optional[int] = 10,
    ) -> Iterator[str]:
        """Yield log lines for a deployment until capture stops or a stop condition is met."""

        streamer = DeploymentLogStreamer(self, poll_interval=poll_interval)
        return streamer.stream(
            deployment_id,
            stop_when=stop_when,
            max_empty_polls=max_empty_polls,
        )

    def list_model_instances(self, deployment_id: Optional[UUID] = None) -> List[ModelInstance]:
        """List all model instances, optionally filtered by deployment ID."""
        params = {"deployment_id": str(deployment_id)} if deployment_id else None
        response = self.client.get("/serving/model_instances", params=params)
        return [ModelInstance.model_validate(item) for item in response]

    def get_model_instance(self, instance_id: UUID) -> ModelInstance:
        """Retrieve a specific model instance by its ID."""
        response = self.client.get(f"/serving/model_instance/{instance_id}")
        return ModelInstance.model_validate(response)

    def get_health(self) -> List[dict]:
        """Get the health of all model deployments."""
        return self.client.get("/serving/health")

    def unload_model(self, request: UnloadModelRequest) -> UnloadModelResponse:
        """Unload a model."""
        response = self.client.post("/unload_model", json=request.model_dump())
        return UnloadModelResponse.model_validate(response)

    def load_model(self, request: LoadModelRequest) -> LoadModelResponse:
        """Load a model."""
        response = self.client.post("/load_model", json=request.model_dump())
        return LoadModelResponse.model_validate(response)


class DeploymentStatusPoller:
    """Utility helper that polls deployment status until completion."""

    def __init__(
        self,
        service: "ServingService",
        *,
        poll_interval: float = 5.0,
        timeout: Optional[float] = 600.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._sleep = sleep_fn
        self._time = time_fn

    def wait_for(
        self,
        deployment_id: Union[str, UUID],
        *,
        desired_status: Iterable[str],
        failure_status: Iterable[str],
    ) -> ModelDeployment:
        deployment_uuid = UUID(str(deployment_id))
        desired = {status.upper() for status in desired_status}
        failures = {status.upper() for status in failure_status}
        start = self._time()
        while True:
            deployment = self._service.get_deployment(deployment_uuid)
            current = (deployment.status or "").upper()
            if current in desired:
                return deployment
            if failures and current in failures:
                raise RuntimeError(
                    f"Deployment {deployment_uuid} entered failure status {deployment.status}"
                )
            if self._timeout is not None and (self._time() - start) > self._timeout:
                raise TimeoutError(
                    f"Timed out waiting for deployment {deployment_uuid} to reach {desired}"
                )
            if self._poll_interval > 0:
                self._sleep(self._poll_interval)


class DeploymentLogStreamer:
    """Simple polling log streamer for serving deployments."""

    def __init__(
        self,
        service: "ServingService",
        *,
        poll_interval: float = 2.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._service = service
        self._poll_interval = poll_interval
        self._sleep = sleep_fn

    def stream(
        self,
        deployment_id: Union[str, UUID],
        *,
        stop_when: Optional[Callable[[ContainerLogResponse], bool]] = None,
        max_empty_polls: Optional[int] = 10,
    ) -> Iterator[str]:
        deployment_uuid = UUID(str(deployment_id))
        last_seen = 0
        empty_polls = 0
        while True:
            response = self._service.get_deployment_logs(deployment_uuid)
            logs = response.logs
            if len(logs) < last_seen:
                last_seen = len(logs)
            new_logs = logs[last_seen:]
            if new_logs:
                for line in new_logs:
                    yield line
                last_seen = len(logs)
                empty_polls = 0
            else:
                empty_polls += 1

            should_stop = False
            if stop_when is not None and stop_when(response):
                should_stop = True
            elif stop_when is None and not response.capture_active and not new_logs:
                should_stop = True

            if should_stop:
                break

            if max_empty_polls is not None and empty_polls >= max_empty_polls:
                break

            if self._poll_interval > 0:
                self._sleep(self._poll_interval)
