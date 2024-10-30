# kamiwaza_client/services/catalog.py

from typing import List, Optional, Dict, Any
from ..schemas.catalog import Dataset, Container
from .base_service import BaseService
import uuid
class CatalogService(BaseService):
    def list_datasets(self) -> List[Dataset]:
        """List all datasets."""
        response = self.client.get("/catalog/dataset")
        return [Dataset.model_validate(item) for item in response]
        
    def create_dataset(
        self,
        dataset_name: str, 
        platform: str, 
        environment: str = "PROD",
        description: str = "", 
        owners: List[str] = None,
        status: str = "CONFORMING",
        location: Optional[str] = None,
        additional_properties: Optional[Dict[str, Any]] = None
    ) -> Dataset:
        """Create a new dataset."""
        # First create the Dataset model as expected by the API endpoint
        dataset = Dataset(
            paths=[dataset_name],
            name=dataset_name,
            id=str(uuid.uuid4()),
            platform=platform,
            actor=owners[0] if owners and owners[0] else "system",
            customProperties={
                "environment": environment,
                "description": description,
                "status": status,
                "location": location,
                **(additional_properties or {})
            },
            removed=False,
            tags=[]
        )

        # Send the Dataset object to the server
        response = self.client.post("/catalog/dataset", json=dataset.model_dump())

        # Parse the response and return a Dataset object
        return Dataset.model_validate(response)

    def list_containers(self) -> List[str]:
        """List all containers."""
        return self.client.get("/catalog/containers")

    def get_dataset(self, datasetname: str) -> List[Dataset]:
        """Retrieve a specific dataset by its name."""
        response = self.client.get(f"/catalog/dataset/{datasetname}")
        return [Dataset.model_validate(item) for item in response]

    def ingest_by_path(self, path: str, dataset_urn: Optional[str] = None, 
                       platform: Optional[str] = None, env: str = "PROD", 
                       location: str = "MAIN", recursive: bool = False, 
                       secrets: Optional[Dict[str, str]] = None) -> None:
        """Ingest a dataset by its path."""
        params = {
            "path": path,
            "dataset_urn": dataset_urn,
            "platform": platform,
            "env": env,
            "location": location,
            "recursive": recursive
        }
        return self.client.post("/catalog/dataset/ingestbypath", params=params, json=secrets)

    def secret_exists(self, secret_name: str) -> bool:
        """Check if a secret exists."""
        return self.client.get(f"/catalog/catalog/secret/exists/{secret_name}")

    def create_secret(self, secret_name: str, secret_value: str, clobber: bool = False) -> None:
        """Create a new secret."""
        params = {
            "secret_name": secret_name,
            "secret_value": secret_value,
            "clobber": clobber
        }
        return self.client.post("/catalogcatalog/dataset/secret", params=params)