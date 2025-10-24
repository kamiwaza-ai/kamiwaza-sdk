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

    The catalog provides metadata management for datasets, containers, and secrets.
    This service offers both V1 (legacy) and V2 (recommended) API methods.

    V2 methods are recommended for new code as they provide:
    - Better URN handling (supports URNs with forward slashes)
    - Cleaner REST semantics
    - Sub-resource operations (e.g., dataset schemas)
    """

    # ===== V1 API Methods (Legacy) =====

    def list_datasets(self, query: Optional[str] = None) -> List[Dataset]:
        """
        List all datasets (V1 API).

        Args:
            query: Optional search query string to filter datasets

        Returns:
            List of Dataset objects
        """
        params = {}
        if query:
            params["query"] = query
        response = self.client.get("/catalog/datasets/", params=params)
        return [Dataset.model_validate(item) for item in response]

    def create_dataset(self, dataset: DatasetCreate) -> str:
        """
        Create a new dataset (V1 API).

        Args:
            dataset: Dataset creation payload

        Returns:
            URN of the created dataset
        """
        response = self.client.post("/catalog/datasets/", json=dataset.model_dump())
        # API returns URN as a string
        return response if isinstance(response, str) else response.get("urn")

    def list_containers(self, query: Optional[str] = None) -> List[Container]:
        """
        List all containers (V1 API).

        Args:
            query: Optional search query string to filter containers

        Returns:
            List of Container objects
        """
        params = {}
        if query:
            params["query"] = query
        response = self.client.get("/catalog/containers/", params=params)
        return [Container.model_validate(item) for item in response]

    def create_container(self, container: ContainerCreate) -> str:
        """
        Create a new container (V1 API).

        Args:
            container: Container creation payload

        Returns:
            URN of the created container
        """
        response = self.client.post("/catalog/containers/", json=container.model_dump())
        # API returns URN as a string
        return response if isinstance(response, str) else response.get("urn")

    # ===== V2 Dataset API Methods (Recommended) =====

    def get_dataset_v2(self, urn: str) -> Dataset:
        """
        Get dataset by URN using V2 API (RECOMMENDED).

        This method properly handles DataHub URNs with forward slashes using
        the V2 API's query parameter approach for reliable URN handling.

        Args:
            urn: Dataset URN (e.g., 'urn:li:dataset:(urn:li:dataPlatform:file,/var/tmp/docs,PROD)')

        Returns:
            Dataset object

        Example:
            >>> dataset = client.catalog.get_dataset_v2(
            ...     'urn:li:dataset:(urn:li:dataPlatform:file,/var/tmp/data,PROD)'
            ... )
        """
        response = self.client.get("/catalog/datasets/by-urn", params={"urn": urn})
        return Dataset.model_validate(response)

    def update_dataset_v2(self, urn: str, update_data: DatasetUpdate) -> Dataset:
        """
        Update dataset using V2 API (RECOMMENDED).

        Args:
            urn: Dataset URN
            update_data: Fields to update

        Returns:
            Updated Dataset object

        Example:
            >>> update = DatasetUpdate(
            ...     description="Updated description",
            ...     tags=["processed", "validated"]
            ... )
            >>> dataset = client.catalog.update_dataset_v2(urn, update)
        """
        response = self.client.patch(
            "/catalog/datasets/by-urn",
            params={"urn": urn},
            json=update_data.model_dump(exclude_unset=True)
        )
        return Dataset.model_validate(response)

    def delete_dataset_v2(self, urn: str) -> None:
        """
        Delete dataset using V2 API (RECOMMENDED).

        Args:
            urn: Dataset URN

        Example:
            >>> client.catalog.delete_dataset_v2('urn:li:dataset:(...)')
        """
        self.client.delete("/catalog/datasets/by-urn", params={"urn": urn})

    def get_dataset_schema_v2(self, urn: str) -> Schema:
        """
        Get dataset schema using V2 API (RECOMMENDED).

        Args:
            urn: Dataset URN

        Returns:
            Schema object containing field definitions

        Example:
            >>> schema = client.catalog.get_dataset_schema_v2('urn:li:dataset:(...)')
            >>> for field in schema.fields:
            ...     print(f"{field.name}: {field.type}")
        """
        response = self.client.get("/catalog/datasets/by-urn/schema", params={"urn": urn})
        return Schema.model_validate(response)

    def update_dataset_schema_v2(self, urn: str, schema: Schema) -> None:
        """
        Update dataset schema using V2 API (RECOMMENDED).

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
            >>> client.catalog.update_dataset_schema_v2(urn, schema)
        """
        self.client.put(
            "/catalog/datasets/by-urn/schema",
            params={"urn": urn},
            json=schema.model_dump()
        )

    # ===== V2 Container API Methods (Recommended) =====

    def get_container_v2(self, urn: str) -> Container:
        """
        Get container by URN using V2 API (RECOMMENDED).

        Args:
            urn: Container URN

        Returns:
            Container object

        Example:
            >>> container = client.catalog.get_container_v2('urn:li:container:(...)')
            >>> print(f"Container has {len(container.datasets)} datasets")
        """
        response = self.client.get("/catalog/containers/by-urn", params={"urn": urn})
        return Container.model_validate(response)

    def update_container_v2(self, urn: str, update_data: ContainerUpdate) -> Container:
        """
        Update container using V2 API (RECOMMENDED).

        Args:
            urn: Container URN
            update_data: Fields to update

        Returns:
            Updated Container object

        Example:
            >>> update = ContainerUpdate(
            ...     description="Production data warehouse",
            ...     tags=["prod", "analytics"]
            ... )
            >>> container = client.catalog.update_container_v2(urn, update)
        """
        response = self.client.patch(
            "/catalog/containers/by-urn",
            params={"urn": urn},
            json=update_data.model_dump(exclude_unset=True)
        )
        return Container.model_validate(response)

    def delete_container_v2(self, urn: str) -> None:
        """
        Delete container using V2 API (RECOMMENDED).

        Args:
            urn: Container URN

        Example:
            >>> client.catalog.delete_container_v2('urn:li:container:(...)')
        """
        self.client.delete("/catalog/containers/by-urn", params={"urn": urn})

    def add_dataset_to_container_v2(self, container_urn: str, dataset_urn: str) -> None:
        """
        Add dataset to container using V2 API (RECOMMENDED).

        Args:
            container_urn: Container URN
            dataset_urn: Dataset URN to add

        Example:
            >>> client.catalog.add_dataset_to_container_v2(
            ...     'urn:li:container:(prod_db)',
            ...     'urn:li:dataset:(urn:li:dataPlatform:file,/data/users.csv,PROD)'
            ... )
        """
        self.client.post(
            "/catalog/containers/by-urn/datasets",
            params={"container_urn": container_urn},
            json={"dataset_urn": dataset_urn}
        )

    def remove_dataset_from_container_v2(
        self,
        container_urn: str,
        dataset_urn: str
    ) -> None:
        """
        Remove dataset from container using V2 API (RECOMMENDED).

        Args:
            container_urn: Container URN
            dataset_urn: Dataset URN to remove

        Example:
            >>> client.catalog.remove_dataset_from_container_v2(
            ...     'urn:li:container:(prod_db)',
            ...     'urn:li:dataset:(urn:li:dataPlatform:file,/data/users.csv,PROD)'
            ... )
        """
        self.client.delete(
            "/catalog/containers/by-urn/datasets",
            params={"container_urn": container_urn, "dataset_urn": dataset_urn}
        )

    # ===== Helper/Utility Methods =====

    def flush_catalog(self) -> None:
        """
        Delete all datasets and containers from the catalog.

        Warning:
            This operation cannot be undone and will remove all data from the catalog.
            Use with extreme caution!
        """
        self.client.delete("/catalog/flush")
