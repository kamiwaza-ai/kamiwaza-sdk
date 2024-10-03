# kamiwaza_client/services/serving.py

from typing import List, Optional, Union
from uuid import UUID
from ..schemas.serving.serving import CreateModelDeployment, ModelDeployment, UIModelDeployment, ModelInstance
from ..schemas.serving.inference import LoadModelRequest, LoadModelResponse, UnloadModelRequest, UnloadModelResponse, SimpleGenerateRequest, SimpleGenerateResponse, GenerateRequest, GenerateResponse
from .base_service import BaseService

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

    def deploy_model(self, deployment_request: CreateModelDeployment) -> Union[UUID, bool]:
        """Deploy a model based on the provided deployment request."""
        response = self.client.post("/serving/deploy_model", json=deployment_request.model_dump())
        return UUID(response) if isinstance(response, str) else response

    def list_deployments(self, model_id: Optional[UUID] = None) -> List[UIModelDeployment]:
        """List all model deployments or filter by model_id."""
        params = {"model_id": str(model_id)} if model_id else None
        response = self.client.get("/serving/deployments", params=params)
        return [UIModelDeployment.model_validate(item) for item in response]

    def get_deployment(self, deployment_id: UUID) -> UIModelDeployment:
        """Get the details of a specific model deployment."""
        response = self.client.get(f"/serving/deployment/{deployment_id}")
        return UIModelDeployment.model_validate(response)

    def stop_deployment(self, deployment_id: UUID, force: Optional[bool] = False) -> bool:
        """Stop a model deployment."""
        return self.client.delete(f"/serving/deployment/{deployment_id}", params={"force": force})

    def get_deployment_status(self, deployment_id: UUID) -> ModelDeployment:
        """Get the status of a specific model deployment."""
        response = self.client.get(f"/serving/deployment/{deployment_id}/status")
        return ModelDeployment.model_validate(response)

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

    def simple_generate(self, request: SimpleGenerateRequest) -> SimpleGenerateResponse:
        """Generate a simple response based on a prompt."""
        response = self.client.post("/simple_generate/", json=request.model_dump())
        return SimpleGenerateResponse.model_validate(response)

    def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Generate a response based on a conversation history."""
        response = self.client.post("/generate", json=request.model_dump())
        return GenerateResponse.model_validate(response)