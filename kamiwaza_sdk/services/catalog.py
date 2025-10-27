# kamiwaza_sdk/services/catalog.py

from typing import List, Optional, Dict, Any
from ..schemas.catalog import (
    Dataset,
    DatasetCreate,
    DatasetUpdate,
    Schema,
    Container,
    ContainerCreate,
    ContainerUpdate,
)
from .base_service import BaseService


class CatalogService(BaseService):
    """
    Service for Kamiwaza Catalog operations.

    The catalog provides metadata management for datasets, containers, and schemas.
    All methods properly handle DataHub URNs including those with forward slashes.

    Dataset Operations:
        - list_datasets() - List all datasets with optional filtering
        - create_dataset() - Create a new dataset
        - get_dataset() - Get dataset by URN
        - update_dataset() - Update dataset metadata
        - delete_dataset() - Delete a dataset
        - get_dataset_schema() - Get dataset schema
        - update_dataset_schema() - Update dataset schema

    Container Operations:
        - list_containers() - List all containers
        - create_container() - Create a new container
        - get_container() - Get container by URN
        - update_container() - Update container metadata
        - delete_container() - Delete a container
        - add_dataset_to_container() - Link dataset to container
        - remove_dataset_from_container() - Unlink dataset from container
    """

    # ===== Dataset Operations =====

    def list_datasets(self, query: Optional[str] = None) -> List[Dataset]:
        """
        List all datasets with optional filtering.

        Args:
            query: Optional search query string to filter datasets

        Returns:
            List of Dataset objects

        Example:
            >>> datasets = client.catalog.list_datasets(query="rag")
            >>> for dataset in datasets:
            ...     print(f"{dataset.name}: {dataset.urn}")
        """
        params = {}
        if query:
            params["query"] = query
        response = self.client.get("/catalog/datasets/", params=params)
        return [Dataset.model_validate(item) for item in response]

    def create_dataset(self, dataset: DatasetCreate) -> str:
        """
        Create a new dataset.

        Args:
            dataset: Dataset creation payload

        Returns:
            URN of the created dataset

        Example:
            >>> from kamiwaza_sdk.schemas.catalog import DatasetCreate
            >>> dataset = DatasetCreate(
            ...     name="my_dataset",
            ...     platform="file",
            ...     environment="PROD",
            ...     description="My dataset"
            ... )
            >>> urn = client.catalog.create_dataset(dataset)
        """
        response = self.client.post("/catalog/datasets/", json=dataset.model_dump())
        # API returns URN as a string
        return response if isinstance(response, str) else response.get("urn")

    def get_dataset(self, urn: str) -> Dataset:
        """
        Get dataset by URN.

        Properly handles DataHub URNs with forward slashes.

        Args:
            urn: Dataset URN (e.g., 'urn:li:dataset:(urn:li:dataPlatform:file,/var/tmp/docs,PROD)')

        Returns:
            Dataset object

        Example:
            >>> dataset = client.catalog.get_dataset(
            ...     'urn:li:dataset:(urn:li:dataPlatform:file,/var/tmp/data,PROD)'
            ... )
        """
        response = self.client.get("/catalog/datasets/by-urn", params={"urn": urn})
        return Dataset.model_validate(response)

    def update_dataset(self, urn: str, update_data: DatasetUpdate) -> Dataset:
        """
        Update dataset metadata.

        Args:
            urn: Dataset URN
            update_data: Fields to update

        Returns:
            Updated Dataset object

        Example:
            >>> from kamiwaza_sdk.schemas.catalog import DatasetUpdate
            >>> update = DatasetUpdate(
            ...     description="Updated description",
            ...     tags=["processed", "validated"]
            ... )
            >>> dataset = client.catalog.update_dataset(urn, update)
        """
        response = self.client.patch(
            "/catalog/datasets/by-urn",
            params={"urn": urn},
            json=update_data.model_dump(exclude_unset=True)
        )
        return Dataset.model_validate(response)

    def delete_dataset(self, urn: str) -> None:
        """
        Delete a dataset.

        Args:
            urn: Dataset URN

        Example:
            >>> client.catalog.delete_dataset('urn:li:dataset:(...)')
        """
        self.client.delete("/catalog/datasets/by-urn", params={"urn": urn})

    def get_dataset_schema(self, urn: str) -> Schema:
        """
        Get dataset schema.

        Args:
            urn: Dataset URN

        Returns:
            Schema object containing field definitions

        Example:
            >>> schema = client.catalog.get_dataset_schema('urn:li:dataset:(...)')
            >>> for field in schema.fields:
            ...     print(f"{field.name}: {field.type}")
        """
        response = self.client.get("/catalog/datasets/by-urn/schema", params={"urn": urn})
        return Schema.model_validate(response)

    def update_dataset_schema(self, urn: str, schema: Schema) -> None:
        """
        Update dataset schema.

        Args:
            urn: Dataset URN
            schema: New schema definition

        Example:
            >>> from kamiwaza_sdk.schemas.catalog import Schema, SchemaField
            >>> schema = Schema(
            ...     name="user_data",
            ...     platform="file",
            ...     fields=[
            ...         SchemaField(name="id", type="string"),
            ...         SchemaField(name="email", type="string")
            ...     ]
            ... )
            >>> client.catalog.update_dataset_schema(urn, schema)
        """
        self.client.put(
            "/catalog/datasets/by-urn/schema",
            params={"urn": urn},
            json=schema.model_dump()
        )

    # ===== Container Operations =====

    def list_containers(self, query: Optional[str] = None) -> List[Container]:
        """
        List all containers with optional filtering.

        Args:
            query: Optional search query string to filter containers

        Returns:
            List of Container objects

        Example:
            >>> containers = client.catalog.list_containers()
            >>> for container in containers:
            ...     print(f"{container.name}: {len(container.datasets)} datasets")
        """
        params = {}
        if query:
            params["query"] = query
        response = self.client.get("/catalog/containers/", params=params)
        return [Container.model_validate(item) for item in response]

    def create_container(self, container: ContainerCreate) -> str:
        """
        Create a new container.

        Args:
            container: Container creation payload

        Returns:
            URN of the created container

        Example:
            >>> from kamiwaza_sdk.schemas.catalog import ContainerCreate
            >>> container = ContainerCreate(
            ...     name="my_container",
            ...     platform="file",
            ...     description="My container"
            ... )
            >>> urn = client.catalog.create_container(container)
        """
        response = self.client.post("/catalog/containers/", json=container.model_dump())
        # API returns URN as a string
        return response if isinstance(response, str) else response.get("urn")

    def get_container(self, urn: str) -> Container:
        """
        Get container by URN.

        Args:
            urn: Container URN

        Returns:
            Container object

        Example:
            >>> container = client.catalog.get_container('urn:li:container:(...)')
            >>> print(f"Container has {len(container.datasets)} datasets")
        """
        response = self.client.get("/catalog/containers/by-urn", params={"urn": urn})
        return Container.model_validate(response)

    def update_container(self, urn: str, update_data: ContainerUpdate) -> Container:
        """
        Update container metadata.

        Args:
            urn: Container URN
            update_data: Fields to update

        Returns:
            Updated Container object

        Example:
            >>> from kamiwaza_sdk.schemas.catalog import ContainerUpdate
            >>> update = ContainerUpdate(
            ...     description="Production data warehouse",
            ...     tags=["prod", "analytics"]
            ... )
            >>> container = client.catalog.update_container(urn, update)
        """
        response = self.client.patch(
            "/catalog/containers/by-urn",
            params={"urn": urn},
            json=update_data.model_dump(exclude_unset=True)
        )
        return Container.model_validate(response)

    def delete_container(self, urn: str) -> None:
        """
        Delete a container.

        Args:
            urn: Container URN

        Example:
            >>> client.catalog.delete_container('urn:li:container:(...)')
        """
        from urllib.parse import quote
        # Use path parameter endpoint instead of /by-urn (which returns 404)
        self.client.delete(f"/catalog/containers/{quote(urn, safe='')}")

    def add_dataset_to_container(self, container_urn: str, dataset_urn: str) -> None:
        """
        Add dataset to container (link relationship).

        Args:
            container_urn: Container URN
            dataset_urn: Dataset URN to add

        Example:
            >>> client.catalog.add_dataset_to_container(
            ...     'urn:li:container:(prod_db)',
            ...     'urn:li:dataset:(urn:li:dataPlatform:file,/data/users.csv,PROD)'
            ... )
        """
        self.client.post(
            "/catalog/containers/by-urn/datasets",
            params={"container_urn": container_urn},
            json={"dataset_urn": dataset_urn}
        )

    def remove_dataset_from_container(
        self,
        container_urn: str,
        dataset_urn: str
    ) -> None:
        """
        Remove dataset from container (unlink relationship).

        Args:
            container_urn: Container URN
            dataset_urn: Dataset URN to remove

        Example:
            >>> client.catalog.remove_dataset_from_container(
            ...     'urn:li:container:(prod_db)',
            ...     'urn:li:dataset:(urn:li:dataPlatform:file,/data/users.csv,PROD)'
            ... )
        """
        self.client.delete(
            "/catalog/containers/by-urn/datasets",
            params={"container_urn": container_urn, "dataset_urn": dataset_urn}
        )

    # ===== Utility Methods =====

    # Note: flush_catalog() endpoint does not exist in backend
    # Manual cleanup required: delete all datasets first, then containers will auto-delete
