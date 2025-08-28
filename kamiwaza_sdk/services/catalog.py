# kamiwaza_client/services/catalog.py

from typing import List, Optional, Dict, Any, Union
from pathlib import Path
from ..schemas.catalog import Dataset, Container
from .base_service import BaseService
import uuid
import os



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
        # Ensure dataset_name is absolute
        dataset_name = os.path.abspath(dataset_name)
        # Generate a deterministic ID based on the dataset name and platform
        dataset_id = f"{platform}_{dataset_name}".replace('/', '_').lower()
        
        # First create the Dataset model as expected by the API endpoint
        dataset = Dataset(
            id=dataset_id,
            platform=platform,
            environment=environment,
            paths=[dataset_name],
            name=dataset_name,
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
        
        # If response is a URN string, store it and return the dataset with the URN
        if isinstance(response, str):
            dataset.urn = response
            return dataset
        
        # If response is a dict, parse it normally
        return Dataset.model_validate(response)

    def list_containers(self) -> List[str]:
        """List all containers."""
        return self.client.get("/catalog/containers")

    def get_dataset(self, datasetname: str) -> List[Dataset]:
        """Retrieve a specific dataset by its name."""
        response = self.client.get(f"/catalog/dataset/{datasetname}")
        return [Dataset.model_validate(item) for item in response]

    def ingest_by_path(
        self, 
        path: Union[str, Path],
        dataset_urn: Optional[str] = None,
        platform: Optional[str] = None,
        env: str = "PROD",
        location: str = "MAIN",
        recursive: bool = False,
        description: Optional[str] = None,
        secrets: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Ingest a dataset by its path.
        
        Args:
            path: Path to the dataset (string or Path object)
            dataset_urn: Optional URN for the dataset
            platform: Platform identifier
            env: Environment (default: PROD)
            location: Location identifier (default: MAIN)
            recursive: Whether to scan recursively (default: False)
            description: Optional dataset description
            secrets: Optional secrets required for ingestion
            
        Raises:
            ValueError: If path doesn't exist or is invalid
            TypeError: If path is not string or Path object
        """
        if isinstance(path, Path):
            path = str(path)
        elif not isinstance(path, str):
            raise TypeError("path must be string or Path object")
            
        if not os.path.exists(path):
            raise ValueError(f"Path does not exist: {path}")
            
        params = {
            "path": path,
            "dataset_urn": dataset_urn,
            "platform": platform,
            "env": env,
            "location": location,
            "recursive": recursive,
            "description": description
        }
        return self.client.post("/catalog/dataset/ingestbypath", 
                              params=params,
                              json=secrets)


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
        return self.client.post("/catalog/catalog/dataset/secret", params=params)
    

    def flush_catalog(self) -> None:
        """
        Delete all datasets and containers from the catalog.
        
        Warning: This operation cannot be undone and will remove all data from the catalog.
        """
        return self.client.delete("/catalog/flush")