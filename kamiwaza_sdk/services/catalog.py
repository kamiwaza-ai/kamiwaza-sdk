"""Catalog service client exposing dataset, container, and secret helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .base_service import BaseService
from ..exceptions import APIError
from ..schemas.catalog import (
    Container,
    ContainerCreate,
    ContainerUpdate,
    Dataset,
    DatasetCreate,
    DatasetUpdate,
    Schema,
    Secret,
    SecretCreate,
)
from ..utils import reveal_secrets


def _encode_path_segment(value: str) -> str:
    """URL-encode a URN for safe inclusion in path segments."""
    return quote(value, safe="")


class DatasetClient(BaseService):
    """Dataset CRUD helpers."""

    _BASE_PATH = "/catalog/datasets"

    def create(self, payload: DatasetCreate) -> str:
        """Create a dataset and return its URN."""
        response = self.client.post(
            f"{self._BASE_PATH}/",
            json=payload.model_dump(exclude_none=True),
        )
        return str(response)

    def list(self, query: Optional[str] = None) -> List[Dataset]:
        params = {"query": query} if query else None
        response = self.client.get(f"{self._BASE_PATH}/", params=params)
        return [Dataset.model_validate(item) for item in response]

    def get(self, dataset_urn: str) -> Dataset:
        response = self.client.get(
            f"{self._BASE_PATH}/by-urn",
            params={"urn": dataset_urn},
        )
        return Dataset.model_validate(response)

    def update(self, dataset_urn: str, update: DatasetUpdate) -> Dataset:
        response = self.client.patch(
            f"{self._BASE_PATH}/by-urn",
            params={"urn": dataset_urn},
            json=update.model_dump(exclude_none=True),
        )
        return Dataset.model_validate(response)

    def delete(self, dataset_urn: str) -> None:
        self.client.delete(
            f"{self._BASE_PATH}/by-urn",
            params={"urn": dataset_urn},
        )

    def get_schema(self, dataset_urn: str) -> Schema:
        response = self.client.get(
            f"{self._BASE_PATH}/by-urn/schema",
            params={"urn": dataset_urn},
        )
        return Schema.model_validate(response)

    def update_schema(self, dataset_urn: str, schema: Schema) -> None:
        self.client.put(
            f"{self._BASE_PATH}/by-urn/schema",
            params={"urn": dataset_urn},
            json=schema.model_dump(exclude_none=True),
        )

    @staticmethod
    def encode_path_urn(dataset_urn: str) -> str:
        """Return a percent-encoded URN suitable for `/v2/{dataset_urn}` paths."""
        return _encode_path_segment(dataset_urn)


class ContainerClient(BaseService):
    """Container CRUD + membership helpers."""

    _BASE_PATH = "/catalog/containers"

    def create(self, payload: ContainerCreate) -> str:
        response = self.client.post(
            f"{self._BASE_PATH}/",
            json=payload.model_dump(exclude_none=True),
        )
        return str(response)

    def list(self, query: Optional[str] = None) -> List[Container]:
        params = {"query": query} if query else None
        response = self.client.get(f"{self._BASE_PATH}/", params=params)
        return [Container.model_validate(item) for item in response]

    def get(self, container_urn: str) -> Container:
        response = self.client.get(
            f"{self._BASE_PATH}/by-urn",
            params={"urn": container_urn},
        )
        return Container.model_validate(response)

    def update(self, container_urn: str, update: ContainerUpdate) -> Container:
        response = self.client.patch(
            f"{self._BASE_PATH}/by-urn",
            params={"urn": container_urn},
            json=update.model_dump(exclude_none=True),
        )
        return Container.model_validate(response)

    def delete(self, container_urn: str) -> None:
        self.client.delete(
            f"{self._BASE_PATH}/by-urn",
            params={"urn": container_urn},
        )

    def add_dataset(self, container_urn: str, dataset_urn: str) -> Dict[str, Any]:
        response = self.client.post(
            f"{self._BASE_PATH}/by-urn/datasets",
            params={"container_urn": container_urn},
            json={"dataset_urn": dataset_urn},
        )
        return response

    def remove_dataset(self, container_urn: str, dataset_urn: str) -> Dict[str, Any]:
        response = self.client.delete(
            f"{self._BASE_PATH}/by-urn/datasets",
            params={
                "container_urn": container_urn,
                "dataset_urn": dataset_urn,
            },
        )
        return response

    @staticmethod
    def encode_path_urn(container_urn: str) -> str:
        return _encode_path_segment(container_urn)


class SecretClient(BaseService):
    """Secret CRUD helpers."""

    _BASE_PATH = "/catalog/secrets"

    def create(self, payload: SecretCreate, *, clobber: bool = False) -> str:
        body = reveal_secrets(payload.model_dump(exclude_none=True))
        response = self.client.post(
            f"{self._BASE_PATH}/",
            params={"clobber": str(clobber).lower()},
            json=body,
        )
        return self._unwrap_secret_urn(response)

    def list(self, query: Optional[str] = None) -> List[Secret]:
        params = {"query": query} if query else None
        response = self.client.get(f"{self._BASE_PATH}/", params=params)
        return [Secret.model_validate(item) for item in response]

    def get(self, secret_urn: str) -> Secret:
        try:
            response = self.client.get(f"{self._BASE_PATH}/v2/{secret_urn}")
        except APIError as exc:
            if exc.status_code != 404:
                raise
            response = self.client.get(
                f"{self._BASE_PATH}/by-urn",
                params={"urn": secret_urn},
            )
        return Secret.model_validate(response)

    def delete(self, secret_urn: str) -> None:
        try:
            self.client.delete(f"{self._BASE_PATH}/v2/{secret_urn}")
        except APIError as exc:
            if exc.status_code != 404:
                raise
            self.client.delete(
                f"{self._BASE_PATH}/by-urn",
                params={"urn": secret_urn},
            )

    @staticmethod
    def encode_path_urn(secret_urn: str) -> str:
        return secret_urn

    @staticmethod
    def _unwrap_secret_urn(response: Any) -> str:
        """Return the backend-issued secret URN without altering its shape."""
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            for key in ("urn", "secret_urn"):
                value = response.get(key)
                if value:
                    return str(value)
        return str(response)


class CatalogService(BaseService):
    """High-level facade for catalog sub-clients."""

    def __init__(self, client):
        super().__init__(client)
        self.datasets = DatasetClient(client)
        self.containers = ContainerClient(client)
        self.secrets = SecretClient(client)

    @staticmethod
    def _normalize_dataset(dataset: Dataset) -> Dataset:
        """Ensure catalog metadata always exposes matching `path`/`location` keys."""

        properties: Dict[str, Any] = dict(dataset.properties or {})
        path = properties.get("path")
        location = properties.get("location")
        updated = False
        if path and not location:
            properties["location"] = path
            updated = True
        elif location and not path:
            properties["path"] = location
            updated = True
        if not updated:
            return dataset
        return dataset.model_copy(update={"properties": properties})

    def encode_urn(self, urn: str) -> str:
        """Expose a helper for percent-encoding URNs."""
        return _encode_path_segment(urn)

    def list_datasets(self, query: Optional[str] = None) -> List[Dataset]:
        """Backward-compatible helper delegating to the dataset client."""
        datasets = self.datasets.list(query=query)
        return [self._normalize_dataset(dataset) for dataset in datasets]

    def get_dataset(self, dataset_urn: str) -> Dataset:
        dataset = self.datasets.get(dataset_urn)
        return self._normalize_dataset(dataset)

    def create_dataset(
        self,
        dataset_name: str,
        platform: str,
        environment: str = "PROD",
        description: str | None = None,
        *,
        tags: Optional[List[str]] = None,
        properties: Optional[Dict[str, Any]] = None,
        container_urn: Optional[str] = None,
        dataset_schema: Optional[Schema] = None,
    ) -> Dataset:
        """Backward-compatible dataset creation wrapper."""
        payload = DatasetCreate(
            name=dataset_name,
            platform=platform,
            environment=environment,
            description=description,
            tags=tags or [],
            properties=properties or {},
            container_urn=container_urn,
            dataset_schema=dataset_schema,
        )
        dataset_urn = self.datasets.create(payload)
        dataset = self.datasets.get(dataset_urn)
        return self._normalize_dataset(dataset)

    def list_containers(self, query: Optional[str] = None) -> List[Container]:
        return self.containers.list(query=query)

    def list_secrets(self, query: Optional[str] = None) -> List[Secret]:
        return self.secrets.list(query=query)

    def health(self) -> Dict[str, Any]:
        return self.client.get("/catalog/health")

    def metadata(self) -> Dict[str, Any]:
        """Return catalog service metadata from the root endpoint."""
        return self.client.get("/catalog/")
