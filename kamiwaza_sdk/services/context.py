"""Client helper for Context Service endpoints."""

from __future__ import annotations

from typing import Any, IO, Mapping, Optional

from .base_service import BaseService


class ContextService(BaseService):
    """SDK wrapper for Context Service lifecycle/search/pipeline APIs."""

    _BASE_PATH = "/context"
    DEFAULT_WORKROOM_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"

    @staticmethod
    def _merge_headers(
        *,
        workroom_id: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        merged: dict[str, str] = dict(headers or {})
        if workroom_id:
            merged["X-Workroom-ID"] = workroom_id
        return merged

    @staticmethod
    def _workroom_params(workroom_id: str | None) -> dict[str, str] | None:
        if not workroom_id:
            return None
        return {"workroom_id": workroom_id}

    # Health

    def health(self) -> dict[str, Any]:
        return self.client.get(f"{self._BASE_PATH}/health")

    # VectorDB lifecycle + operations

    def list_vectordbs(self, *, workroom_id: str | None = None) -> list[dict[str, Any]]:
        return self.client.get(
            f"{self._BASE_PATH}/vectordbs",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def get_vectordb(
        self,
        vectordb_id: str,
        *,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.get(
            f"{self._BASE_PATH}/vectordbs/{vectordb_id}",
            params=self._workroom_params(workroom_id),
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def create_vectordb(
        self,
        *,
        name: str,
        engine: str,
        config: Optional[dict[str, Any]] = None,
        workroom_id: str | None = None,
        replicas: int = 1,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "engine": engine,
            "replicas": replicas,
        }
        if config is not None:
            payload["config"] = config
        if workroom_id is not None:
            payload["workroom_id"] = workroom_id
        return self.client.post(
            f"{self._BASE_PATH}/vectordbs",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def update_vectordb(
        self,
        vectordb_id: str,
        *,
        config: Optional[dict[str, Any]] = None,
        replicas: int | None = None,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if config is not None:
            payload["config"] = config
        if replicas is not None:
            payload["replicas"] = replicas
        return self.client.put(
            f"{self._BASE_PATH}/vectordbs/{vectordb_id}",
            json=payload,
            params=self._workroom_params(workroom_id),
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def scale_vectordb(
        self,
        vectordb_id: str,
        *,
        replicas: int,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.post(
            f"{self._BASE_PATH}/vectordbs/{vectordb_id}/scale",
            json={"replicas": replicas},
            params=self._workroom_params(workroom_id),
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def delete_vectordb(
        self,
        vectordb_id: str,
        *,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.delete(
            f"{self._BASE_PATH}/vectordbs/{vectordb_id}",
            params=self._workroom_params(workroom_id),
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def query_vectors(
        self,
        vectordb_id: str,
        *,
        collection_name: str,
        vectors: list[list[float]],
        limit: int = 10,
        params: Optional[dict[str, Any]] = None,
        output_fields: Optional[list[str]] = None,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "collection_name": collection_name,
            "vectors": vectors,
            "limit": limit,
        }
        if params is not None:
            payload["params"] = params
        if output_fields is not None:
            payload["output_fields"] = output_fields
        return self.client.post(
            f"{self._BASE_PATH}/vectordbs/{vectordb_id}/query",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def query_vectors_global(
        self,
        *,
        vectordb_id: str,
        collection_name: str,
        vectors: list[list[float]],
        limit: int = 10,
        params: Optional[dict[str, Any]] = None,
        output_fields: Optional[list[str]] = None,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "vectordb_id": vectordb_id,
            "collection_name": collection_name,
            "vectors": vectors,
            "limit": limit,
        }
        if params is not None:
            payload["params"] = params
        if output_fields is not None:
            payload["output_fields"] = output_fields
        return self.client.post(
            f"{self._BASE_PATH}/vectordbs/query",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def insert_vectors(
        self,
        vectordb_id: str,
        *,
        collection_name: str,
        vectors: list[list[float]],
        metadata: list[dict[str, Any]],
        field_list: Optional[list[list[Any]]] = None,
        create_if_missing: bool = True,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "collection_name": collection_name,
            "vectors": vectors,
            "metadata": metadata,
            "create_if_missing": create_if_missing,
        }
        if field_list is not None:
            payload["field_list"] = field_list
        return self.client.post(
            f"{self._BASE_PATH}/vectordbs/{vectordb_id}/insert",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def insert_vectors_global(
        self,
        *,
        vectordb_id: str,
        collection_name: str,
        vectors: list[list[float]],
        metadata: list[dict[str, Any]],
        field_list: Optional[list[list[Any]]] = None,
        create_if_missing: bool = True,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "vectordb_id": vectordb_id,
            "collection_name": collection_name,
            "vectors": vectors,
            "metadata": metadata,
            "create_if_missing": create_if_missing,
        }
        if field_list is not None:
            payload["field_list"] = field_list
        return self.client.post(
            f"{self._BASE_PATH}/vectordbs/insert",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    # Ontology lifecycle + operations

    def list_ontologies(self, *, workroom_id: str | None = None) -> list[dict[str, Any]]:
        return self.client.get(
            f"{self._BASE_PATH}/ontologies",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def get_ontology(
        self,
        ontology_id: str,
        *,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.get(
            f"{self._BASE_PATH}/ontologies/{ontology_id}",
            params=self._workroom_params(workroom_id),
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def create_ontology(
        self,
        *,
        name: str,
        backend: str,
        config: Optional[dict[str, Any]] = None,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name, "backend": backend}
        if config is not None:
            payload["config"] = config
        if workroom_id is not None:
            payload["workroom_id"] = workroom_id
        return self.client.post(
            f"{self._BASE_PATH}/ontologies",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def delete_ontology(
        self,
        ontology_id: str,
        *,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.delete(
            f"{self._BASE_PATH}/ontologies/{ontology_id}",
            params=self._workroom_params(workroom_id),
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def add_knowledge(
        self,
        ontology_id: str,
        *,
        group_id: str,
        messages: list[dict[str, Any]],
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.post(
            f"{self._BASE_PATH}/ontologies/{ontology_id}/knowledge",
            json={"group_id": group_id, "messages": messages},
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def add_entity(
        self,
        ontology_id: str,
        *,
        group_id: str,
        name: str,
        entity_type: str,
        summary: str | None = None,
        properties: Optional[dict[str, Any]] = None,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "group_id": group_id,
            "name": name,
            "entity_type": entity_type,
        }
        if summary is not None:
            payload["summary"] = summary
        if properties is not None:
            payload["properties"] = properties
        return self.client.post(
            f"{self._BASE_PATH}/ontologies/{ontology_id}/entity",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def search_knowledge(
        self,
        ontology_id: str,
        *,
        query: str,
        group_ids: list[str],
        max_results: int = 10,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.post(
            f"{self._BASE_PATH}/ontologies/{ontology_id}/search",
            json={"query": query, "group_ids": group_ids, "max_results": max_results},
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def get_memory(
        self,
        ontology_id: str,
        *,
        group_id: str,
        query: str,
        max_facts: int = 10,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.post(
            f"{self._BASE_PATH}/ontologies/{ontology_id}/memory",
            json={"group_id": group_id, "query": query, "max_facts": max_facts},
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def get_episodes(
        self,
        ontology_id: str,
        *,
        group_id: str,
        last_n: int = 10,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.get(
            f"{self._BASE_PATH}/ontologies/{ontology_id}/episodes/{group_id}",
            params={"last_n": last_n},
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def delete_group(
        self,
        ontology_id: str,
        *,
        group_id: str,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.delete(
            f"{self._BASE_PATH}/ontologies/{ontology_id}/groups/{group_id}",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def ontology_health(
        self,
        ontology_id: str,
        *,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.get(
            f"{self._BASE_PATH}/ontologies/{ontology_id}/health",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    # Collections + pipelines + search + upload

    def list_collections(self, *, workroom_id: str) -> list[dict[str, Any]]:
        return self.client.get(
            f"{self._BASE_PATH}/collections/",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def create_collection(
        self,
        *,
        workroom_id: str,
        name: str,
        dimension: int = 384,
        description: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name, "dimension": dimension}
        if description is not None:
            payload["description"] = description
        return self.client.post(
            f"{self._BASE_PATH}/collections/",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def get_collection(self, *, workroom_id: str, collection_name: str) -> dict[str, Any]:
        return self.client.get(
            f"{self._BASE_PATH}/collections/{collection_name}",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def delete_collection(
        self, *, workroom_id: str, collection_name: str
    ) -> dict[str, Any] | None:
        return self.client.delete(
            f"{self._BASE_PATH}/collections/{collection_name}",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def create_pipeline_job(
        self,
        *,
        workroom_id: str,
        files: list[dict[str, Any]],
        config: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return self.client.post(
            f"{self._BASE_PATH}/pipelines/",
            json={"files": files, "config": config or {}},
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def list_pipeline_jobs(
        self,
        *,
        workroom_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        return self.client.get(
            f"{self._BASE_PATH}/pipelines/",
            params=params,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def get_pipeline_job(self, *, workroom_id: str, job_id: str) -> dict[str, Any]:
        return self.client.get(
            f"{self._BASE_PATH}/pipelines/{job_id}",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def cancel_pipeline_job(
        self, *, workroom_id: str, job_id: str
    ) -> dict[str, Any] | None:
        return self.client.delete(
            f"{self._BASE_PATH}/pipelines/{job_id}",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def get_supported_file_types(self) -> list[str]:
        return self.client.get(f"{self._BASE_PATH}/pipelines/supported-types")

    def search(
        self,
        *,
        workroom_id: str,
        query: str,
        collection_name: str | None = None,
        top_k: int = 10,
        score_threshold: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query, "top_k": top_k}
        if collection_name is not None:
            payload["collection_name"] = collection_name
        if score_threshold is not None:
            payload["score_threshold"] = score_threshold
        return self.client.post(
            f"{self._BASE_PATH}/search",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def retrieve(
        self,
        *,
        workroom_id: str,
        query: str,
        collection_names: list[str] | None = None,
        top_k: int = 5,
        score_threshold: float = 0.7,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "score_threshold": score_threshold,
        }
        if collection_names is not None:
            payload["collection_names"] = collection_names
        return self.client.post(
            f"{self._BASE_PATH}/retrieve",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def agentic_search(
        self,
        *,
        workroom_id: str,
        query: str,
        vectordb_ids: list[str] | None = None,
        collection: str = "default",
        ontology_id: str | None = None,
        group_ids: list[str] | None = None,
        max_iterations: int = 3,
        relevance_threshold: float = 0.7,
        top_k: int = 10,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "vectordb_ids": vectordb_ids or [],
            "collection": collection,
            "max_iterations": max_iterations,
            "relevance_threshold": relevance_threshold,
            "top_k": top_k,
        }
        if ontology_id is not None:
            payload["ontology_id"] = ontology_id
        if group_ids is not None:
            payload["group_ids"] = group_ids
        return self.client.post(
            f"{self._BASE_PATH}/agentic/search",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def upload_file(
        self,
        *,
        workroom_id: str,
        filename: str,
        file_content: bytes | IO[bytes],
        content_type: str = "application/octet-stream",
        collection_name: str | None = None,
        source_urn: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if collection_name is not None:
            params["collection_name"] = collection_name
        if source_urn is not None:
            params["source_urn"] = source_urn
        files = {"file": (filename, file_content, content_type)}
        return self.client.post(
            f"{self._BASE_PATH}/upload/",
            files=files,
            params=params or None,
            headers=self._merge_headers(workroom_id=workroom_id),
        )
