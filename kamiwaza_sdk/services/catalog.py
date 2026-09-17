"""Catalog service client exposing dataset, container, and secret helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypeVar
from urllib.parse import quote

from pydantic import BaseModel

from .base_service import BaseService
from .federation_credentials import federation_credential_headers
from .jobs_routing import _validate_target_cluster
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
from ..schemas.connector_spec import ConnectorSpec
from ..utils import reveal_secrets


#: Model parsed out of a catalogue response.
ModelT = TypeVar("ModelT", bound=BaseModel)


def _encode_path_segment(value: str) -> str:
    """URL-encode a URN for safe inclusion in path segments."""
    return quote(value, safe="")


class _ByUrnClient(BaseService):
    """Shared by-URN request shapes for the catalogue sub-clients.

    Datasets, containers and secrets are addressed the same way — a ``by-urn``
    route taking the URN as a query parameter — so the request plumbing lives
    here once. Each sub-client keeps its own public methods, because the models
    they parse and the promises they make differ.
    """

    _BASE_PATH: str

    def _list_by_query(
        self, model: type[ModelT], query: Optional[str]
    ) -> List[ModelT]:
        """List entries, optionally filtered by a free-text query.

        Args:
            model: Pydantic model to parse each entry into.
            query: Free-text filter, or ``None`` for everything.

        Returns:
            List[ModelT]: Parsed entries.
        """
        params = {"query": query} if query else None
        response = self.client.get(f"{self._BASE_PATH}/", params=params)
        return [model.model_validate(item) for item in response]

    def _get_by_urn(
        self, model: type[ModelT], urn: str, *, suffix: str = ""
    ) -> ModelT:
        """Fetch one entry, or a sub-resource of it, by URN.

        Args:
            model: Pydantic model to parse the response into.
            urn: URN of the entry.
            suffix: Sub-resource path appended to the by-URN route, such as
                ``/schema``.

        Returns:
            ModelT: The parsed response.
        """
        response = self.client.get(
            f"{self._BASE_PATH}/by-urn{suffix}",
            params={"urn": urn},
        )
        return model.model_validate(response)

    def _patch_by_urn(
        self, model: type[ModelT], urn: str, payload: Any
    ) -> ModelT:
        """Update one entry by URN, sending only the fields that are set.

        Args:
            model: Pydantic model to parse the response into.
            urn: URN of the entry to update.
            payload: Update model whose unset fields are omitted.

        Returns:
            ModelT: The parsed, updated entry.
        """
        response = self.client.patch(
            f"{self._BASE_PATH}/by-urn",
            params={"urn": urn},
            json=payload.model_dump(exclude_none=True),
        )
        return model.model_validate(response)

    def _delete_by_urn(self, urn: str) -> None:
        """Delete one entry by URN.

        Args:
            urn: URN of the entry to delete.
        """
        self.client.delete(f"{self._BASE_PATH}/by-urn", params={"urn": urn})


class DatasetClient(_ByUrnClient):
    """Dataset CRUD helpers."""

    _BASE_PATH = "/catalog/datasets"

    def create(self, payload: DatasetCreate) -> str:
        """Create a dataset and return its URN."""
        response = self.client.post(
            f"{self._BASE_PATH}/",
            json=payload.model_dump(exclude_none=True),
        )
        urn = str(response)
        note = getattr(self.client, "_note_recent_dataset_change", None)
        if callable(note):
            note(urn)
        return urn

    def register_from_spec(self, spec: "ConnectorSpec | dict[str, Any]") -> str:
        """Register a standing governed dataset from a connector spec (CSE).

        ``spec`` may be a :class:`ConnectorSpec` or a plain dict. Returns the
        new dataset URN. The engine validates the spec (``extra='forbid'``) and
        pulls nothing at registration time; credentials are referenced by a
        catalog secret URN, never inlined.
        """
        if isinstance(spec, ConnectorSpec):
            body = spec.model_dump(exclude_none=True, mode="json")
        else:
            body = dict(spec)
        response = self.client.post(
            f"{self._BASE_PATH}/register-from-spec", json=body
        )
        urn = self._unwrap_dataset_urn(response)
        note = getattr(self.client, "_note_recent_dataset_change", None)
        if callable(note):
            note(urn)
        return urn

    def add_publisher(self, subject_user_id: str) -> None:
        """Grant a subject the cluster-wide publisher relation for datasets.

        Administrator only. The relation lets the subject publish any dataset
        in the catalogue, not one dataset.
        """
        self.client.post(
            f"{self._BASE_PATH}/publishers",
            json={"subject_user_id": subject_user_id},
        )

    def remove_publisher(self, subject_user_id: str) -> None:
        """Revoke a subject's cluster-wide dataset publisher relation.

        Administrator only. The subject keeps any dataset it already
        published; this ends its ability to publish more.
        """
        self.client.delete(
            f"{self._BASE_PATH}/publishers",
            json={"subject_user_id": subject_user_id},
        )

    @staticmethod
    def _unwrap_dataset_urn(response: Any) -> str:
        """Return the dataset URN from a register-from-spec response."""
        if isinstance(response, dict):
            for key in ("dataset_urn", "urn"):
                value = response.get(key)
                if value:
                    return str(value)
        return str(response)

    def list(
        self,
        query: Optional[str] = None,
        *,
        target_cluster: Optional[str] = None,
    ) -> List[Dataset]:
        """List local datasets, or receiver-authorized datasets through mesh.

        Args:
            query: Free-text filter. Omit it to list every dataset.
            target_cluster: Federation selector to read through instead of the
                local catalog. Omit it for local datasets.

        Returns:
            List[Dataset]: Matching datasets.
        """
        params = {"query": query} if query else None
        path = f"{self._BASE_PATH}/"
        request_kwargs: Dict[str, Any] = {"params": params}
        if target_cluster is not None:
            selector = _validate_target_cluster(target_cluster)
            path = f"/mesh/{quote(selector, safe='')}/api{path}"
            headers = federation_credential_headers(selector)
            if headers:
                request_kwargs["headers"] = headers
        response = self.client.get(path, **request_kwargs)
        return [Dataset.model_validate(item) for item in response]

    def get(self, dataset_urn: str) -> Dataset:
        """Fetch one catalogued dataset by its URN.

        Args:
            dataset_urn: URN of the dataset.

        Returns:
            Dataset: The dataset's catalogue entry.
        """
        return self._get_by_urn(Dataset, dataset_urn)

    def update(self, dataset_urn: str, update: DatasetUpdate) -> Dataset:
        """Update a catalogued dataset's metadata, leaving its data untouched.

        Args:
            dataset_urn: URN of the dataset to update.
            update: Fields to change. Unset fields are left alone.

        Returns:
            Dataset: The dataset as it now stands.
        """
        dataset = self._patch_by_urn(Dataset, dataset_urn, update)
        note = getattr(self.client, "_note_recent_dataset_change", None)
        if callable(note):
            note(dataset.urn)
        return dataset

    def delete(self, dataset_urn: str) -> None:
        """Remove a dataset's catalogue entry, leaving its stored data alone.

        Args:
            dataset_urn: URN of the dataset to remove.
        """
        self._delete_by_urn(dataset_urn)

    def get_schema(self, dataset_urn: str) -> Schema:
        """Fetch the field schema recorded for a catalogued dataset.

        Args:
            dataset_urn: URN of the dataset.

        Returns:
            Schema: The dataset's recorded schema.
        """
        return self._get_by_urn(Schema, dataset_urn, suffix="/schema")

    def update_schema(self, dataset_urn: str, schema: Schema) -> None:
        """Replace the field schema recorded for a catalogued dataset.

        Args:
            dataset_urn: URN of the dataset.
            schema: The schema to record in place of the current one.
        """
        self.client.put(
            f"{self._BASE_PATH}/by-urn/schema",
            params={"urn": dataset_urn},
            json=schema.model_dump(exclude_none=True),
        )

    @staticmethod
    def encode_path_urn(dataset_urn: str) -> str:
        """Return a percent-encoded URN suitable for `/v2/{dataset_urn}` paths."""
        return _encode_path_segment(dataset_urn)


class ContainerClient(_ByUrnClient):
    """Container CRUD + membership helpers."""

    _BASE_PATH = "/catalog/containers"

    def create(self, payload: ContainerCreate) -> str:
        """Create a catalogue container to group related datasets.

        Args:
            payload: The container's name and metadata.

        Returns:
            str: URN of the created container.
        """
        response = self.client.post(
            f"{self._BASE_PATH}/",
            json=payload.model_dump(exclude_none=True),
        )
        return str(response)

    def list(self, query: Optional[str] = None) -> List[Container]:
        """List catalogue containers, optionally narrowed by a search query.

        Args:
            query: Free-text filter. Omit it to list every container.

        Returns:
            List[Container]: Matching containers.
        """
        return self._list_by_query(Container, query)

    def get(self, container_urn: str) -> Container:
        """Fetch one catalogue container by its URN.

        Args:
            container_urn: URN of the container.

        Returns:
            Container: The container's catalogue entry.
        """
        return self._get_by_urn(Container, container_urn)

    def update(self, container_urn: str, update: ContainerUpdate) -> Container:
        """Update a container's metadata without changing its membership.

        Args:
            container_urn: URN of the container to update.
            update: Fields to change. Unset fields are left alone.

        Returns:
            Container: The container as it now stands.
        """
        return self._patch_by_urn(Container, container_urn, update)

    def delete(self, container_urn: str) -> None:
        """Remove a container, leaving the datasets it grouped in place.

        Args:
            container_urn: URN of the container to remove.
        """
        self._delete_by_urn(container_urn)

    def add_dataset(self, container_urn: str, dataset_urn: str) -> Dict[str, Any]:
        """Add one dataset to a container's membership.

        Args:
            container_urn: URN of the container.
            dataset_urn: URN of the dataset to add.

        Returns:
            Dict[str, Any]: The platform's membership response.
        """
        response = self.client.post(
            f"{self._BASE_PATH}/by-urn/datasets",
            params={"container_urn": container_urn},
            json={"dataset_urn": dataset_urn},
        )
        return response

    def remove_dataset(self, container_urn: str, dataset_urn: str) -> Dict[str, Any]:
        """Remove one dataset from a container, without deleting the dataset.

        Args:
            container_urn: URN of the container.
            dataset_urn: URN of the dataset to remove from it.

        Returns:
            Dict[str, Any]: The platform's membership response.
        """
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
        """Return a percent-encoded container URN for use in a path segment.

        Args:
            container_urn: URN to encode.

        Returns:
            str: The URN, safe to embed in a request path.
        """
        return _encode_path_segment(container_urn)


class SecretClient(BaseService):
    """Secret CRUD helpers."""

    _BASE_PATH = "/catalog/secrets"

    def create(self, payload: SecretCreate, *, clobber: bool = False) -> str:
        """Store a secret in the catalogue and return its URN.

        The value is sent to the platform and never returned by any read on
        this client: subsequent lookups carry the secret's metadata only.

        Args:
            payload: Name, value, owner and description of the secret.
            clobber: Overwrite an existing secret of the same name. Defaults
                to false, so a name collision fails rather than replacing a
                value something else may depend on.

        Returns:
            str: URN of the stored secret.
        """
        body = reveal_secrets(payload.model_dump(exclude_none=True))
        response = self.client.post(
            f"{self._BASE_PATH}/",
            params={"clobber": str(clobber).lower()},
            json=body,
        )
        return self._unwrap_secret_urn(response)

    def list(self, query: Optional[str] = None) -> List[Secret]:
        """List stored secrets by metadata, never their values.

        Args:
            query: Free-text filter. Omit it to list every secret.

        Returns:
            List[Secret]: Matching secrets, carrying URN, name, owner,
            description and timestamps — no value.
        """
        params = {"query": query} if query else None
        response = self.client.get(f"{self._BASE_PATH}/", params=params)
        return [Secret.model_validate(item) for item in response]

    def get(self, secret_urn: str) -> Secret:
        """Fetch one secret's metadata by URN, without its value.

        Falls back to the by-URN query route when the versioned path returns
        404, so a platform on either route shape works.

        Args:
            secret_urn: URN of the secret.

        Returns:
            Secret: The secret's metadata. The value is not included.
        """
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
        """Delete a stored secret, breaking anything still resolving it.

        Args:
            secret_urn: URN of the secret to delete.
        """
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
        """Return a secret URN for use in a path segment, unencoded.

        Secret URNs are already path-safe on this route, so this returns the
        URN unchanged. It exists so callers can treat every catalogue
        sub-client the same way.

        Args:
            secret_urn: URN to use in a path.

        Returns:
            str: The URN, unchanged.
        """
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
        """Fetch one catalogued dataset by URN, with its fields normalised.

        Args:
            dataset_urn: URN of the dataset.

        Returns:
            Dataset: The dataset's catalogue entry.
        """
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
        """Register a dataset in the catalogue and return the stored entry.

        Args:
            dataset_name: Name to register.
            platform: Platform the dataset belongs to.
            environment: Catalogue environment.
            description: Optional description.
            tags: Optional tags.
            properties: Optional free-form properties.
            container_urn: Container to add the dataset to, when grouping it.
            dataset_schema: Field schema to record alongside the dataset.

        Returns:
            Dataset: The registered dataset, read back after creation.
        """
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

    def register_from_spec(self, spec: "ConnectorSpec | dict[str, Any]") -> str:
        """Register a governed dataset from a connector spec; return its URN (CSE)."""
        return self.datasets.register_from_spec(spec)

    def add_publisher(self, subject_user_id: str) -> None:
        """Grant the dataset publisher relation, from the catalogue entry point.

        Administrator only. The same act as ``catalog.datasets.add_publisher``,
        reachable without naming the datasets sub-client.
        """
        self.datasets.add_publisher(subject_user_id)

    def remove_publisher(self, subject_user_id: str) -> None:
        """Revoke the dataset publisher relation, from the catalogue entry point.

        Administrator only. The same act as
        ``catalog.datasets.remove_publisher``, reachable without naming the
        datasets sub-client.
        """
        self.datasets.remove_publisher(subject_user_id)

    def list_containers(self, query: Optional[str] = None) -> List[Container]:
        """List catalogue containers, optionally narrowed by a search query.

        Args:
            query: Free-text filter. Omit it to list every container.

        Returns:
            List[Container]: Matching containers.
        """
        return self.containers.list(query=query)

    def list_secrets(self, query: Optional[str] = None) -> List[Secret]:
        """List stored secrets by metadata, never their values.

        Args:
            query: Free-text filter. Omit it to list every secret.

        Returns:
            List[Secret]: Matching secrets, without any secret value.
        """
        return self.secrets.list(query=query)

    def health(self) -> Dict[str, Any]:
        """Report whether the catalogue service is answering requests.

        Returns:
            Dict[str, Any]: The service's health payload.
        """
        return self.client.get("/catalog/health")

    def metadata(self) -> Dict[str, Any]:
        """Return catalog service metadata from the root endpoint."""
        return self.client.get("/catalog/")
