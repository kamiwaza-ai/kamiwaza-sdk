# kamiwaza_client/services/serving.py

from typing import Dict, List, Optional, Union
from uuid import UUID

class ServingService:
    def __init__(self, client):
        self.client = client

    def start_ray(self, address: Optional[str] = None, runtime_env: Optional[Dict] = None, options: Optional[Dict] = None) -> None:
        """Start Ray with given parameters."""
        data = {
            "address": address,
            "runtime_env": runtime_env,
            "options": options
        }
        return self.client.post("/serving/start", json=data)

    def get_status(self) -> Dict:
        """Get the status of Ray."""
        return self.client.get("/serving/status")

    def estimate_model_vram(self, deployment_request: Dict) -> Dict[str, float]:
        """Estimate the VRAM required for a model deployment."""
        return self.client.post("/serving/estimate_model_vram", json=deployment_request)

    def deploy_model(self, deployment_request: Dict) -> Union[UUID, bool]:
        """Deploy a model based on the provided deployment request."""
        return self.client.post("/serving/deploy_model", json=deployment_request)

    def list_deployments(self, model_id: Optional[UUID] = None) -> List[Dict]:
        """List all model deployments or filter by model_id."""
        params = {"model_id": str(model_id)} if model_id else None
        return self.client.get("/serving/deployments", params=params)

    def get_deployment(self, deployment_id: UUID) -> Dict:
        """Get the details of a specific model deployment."""
        return self.client.get(f"/serving/deployment/{deployment_id}")

    def stop_deployment(self, deployment_id: UUID, force: Optional[bool] = False) -> bool:
        """Stop a model deployment."""
        return self.client.delete(f"/serving/deployment/{deployment_id}", params={"force": force})

    def get_deployment_status(self, deployment_id: UUID) -> Dict:
        """Get the status of a specific model deployment."""
        return self.client.get(f"/serving/deployment/{deployment_id}/status")

    def list_model_instances(self, deployment_id: Optional[UUID] = None) -> List[Dict]:
        """List all model instances, optionally filtered by deployment ID."""
        params = {"deployment_id": str(deployment_id)} if deployment_id else None
        return self.client.get("/serving/model_instances", params=params)

    def get_model_instance(self, instance_id: UUID) -> Dict:
        """Retrieve a specific model instance by its ID."""
        return self.client.get(f"/serving/model_instance/{instance_id}")

    def get_health(self) -> List[Dict[str, str]]:
        """Get the health of all model deployments."""
        return self.client.get("/serving/health")