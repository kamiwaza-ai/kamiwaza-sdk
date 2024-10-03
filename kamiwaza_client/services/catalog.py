# kamiwaza_client/services/catalog.py

from typing import Dict, List, Optional
from uuid import UUID
from .base_service import BaseService

class CatalogService(BaseService):
    def list_datasets(self) -> List[Dict]:
        """List all datasets."""
        return self.client.get("/catalog/dataset")

    def create_dataset(self, dataset: Dict) -> Dict:
        """Create a new dataset."""
        return self.client.post("/catalog/dataset", json=dataset)

    def list_containers(self) -> List[str]:
        """List all containers."""
        return self.client.get("/catalog/containers")

    def get_dataset(self, datasetname: str) -> List[Dict]:
        """Retrieve a specific dataset by its name."""
        return self.client.get(f"/catalog/dataset/{datasetname}")

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