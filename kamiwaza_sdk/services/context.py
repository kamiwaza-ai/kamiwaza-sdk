"""Client helper for Context Service endpoints."""

from __future__ import annotations

import base64
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

    def list_collections(
        self,
        *,
        workroom_id: str,
        vectordb_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] | None = None
        if vectordb_id is not None:
            params = {"vectordb_id": vectordb_id}
        return self.client.get(
            f"{self._BASE_PATH}/collections/",
            params=params,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def create_collection(
        self,
        *,
        workroom_id: str,
        name: str,
        dimension: int = 384,
        description: str | None = None,
        vectordb_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name, "dimension": dimension}
        if description is not None:
            payload["description"] = description
        if vectordb_id is not None:
            payload["vectordb_id"] = vectordb_id
        return self.client.post(
            f"{self._BASE_PATH}/collections/",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def get_collection(
        self,
        *,
        workroom_id: str,
        collection_name: str,
        vectordb_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] | None = None
        if vectordb_id is not None:
            params = {"vectordb_id": vectordb_id}
        return self.client.get(
            f"{self._BASE_PATH}/collections/{collection_name}",
            params=params,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def delete_collection(
        self,
        *,
        workroom_id: str,
        collection_name: str,
        vectordb_id: str | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] | None = None
        if vectordb_id is not None:
            params = {"vectordb_id": vectordb_id}
        return self.client.delete(
            f"{self._BASE_PATH}/collections/{collection_name}",
            params=params,
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

    def delete_pipeline_job(
        self, *, workroom_id: str, job_id: str
    ) -> dict[str, Any] | None:
        """Destructively delete a pipeline job and its recorded history.

        Maps to ``DELETE /context/pipelines/{job_id}``: pending/running jobs are
        cancelled first, then the job and its history are removed. For a
        non-destructive cancel that preserves history, use
        :meth:`cancel_pipeline_job`.
        """
        return self.client.delete(
            f"{self._BASE_PATH}/pipelines/{job_id}",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def cancel_pipeline_job(self, *, workroom_id: str, job_id: str) -> dict[str, Any]:
        """Gracefully cancel a pipeline job, preserving its recorded history.

        Maps to ``POST /context/pipelines/{job_id}/cancel``. Distinct from
        :meth:`delete_pipeline_job`, which hard-deletes the job and its history.
        """
        return self.client.post(
            f"{self._BASE_PATH}/pipelines/{job_id}/cancel",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def get_import_options(self, *, workroom_id: str | None = None) -> dict[str, Any]:
        """Get aggregated provider-neutral import options for the workroom.

        Maps to ``GET /context/pipelines/import-options``. The workroom scope is
        optional; omit it for Global-level import options.
        """
        return self.client.get(
            f"{self._BASE_PATH}/pipelines/import-options",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def evaluate_import_options(
        self,
        *,
        sources: list[dict[str, Any]],
        config: Optional[dict[str, Any]] = None,
        workroom_id: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate selected source descriptors against Context import rules.

        Maps to ``POST /context/pipelines/import-options``. Returns the import
        options plus per-source validation (``can_submit``, ``validation_issues``,
        ``normalized_config``).
        """
        payload: dict[str, Any] = {"sources": sources}
        if config is not None:
            payload["config"] = config
        return self.client.post(
            f"{self._BASE_PATH}/pipelines/import-options",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def create_source_import_job(
        self,
        *,
        workroom_id: str,
        sources: list[dict[str, Any]],
        config: Optional[dict[str, Any]] = None,
        callback: Optional[dict[str, Any]] = None,
        idempotency_key: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Create and start a provider-neutral source-import job.

        Maps to ``POST /context/pipelines/imports``.
        """
        payload: dict[str, Any] = {"sources": sources, "force": force}
        if config is not None:
            payload["config"] = config
        if callback is not None:
            payload["callback"] = callback
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        return self.client.post(
            f"{self._BASE_PATH}/pipelines/imports",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def list_import_items(self, *, workroom_id: str) -> dict[str, Any]:
        """List the workroom-wide source-import inventory/history.

        Maps to ``GET /context/pipelines/items``.
        """
        return self.client.get(
            f"{self._BASE_PATH}/pipelines/items",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def rerun_import_items(
        self,
        *,
        workroom_id: str,
        item_keys: list[str],
        config: Optional[dict[str, Any]] = None,
        callback: Optional[dict[str, Any]] = None,
        idempotency_key: str | None = None,
        force: bool = True,
    ) -> dict[str, Any]:
        """Rerun selected inventory items by their recorded source descriptors.

        Maps to ``POST /context/pipelines/items/rerun``.
        """
        payload: dict[str, Any] = {"item_keys": item_keys, "force": force}
        if config is not None:
            payload["config"] = config
        if callback is not None:
            payload["callback"] = callback
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        return self.client.post(
            f"{self._BASE_PATH}/pipelines/items/rerun",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def list_pipeline_job_items(
        self, *, workroom_id: str, job_id: str
    ) -> dict[str, Any]:
        """List canonical per-item statuses for one pipeline job.

        Maps to ``GET /context/pipelines/{job_id}/items``.
        """
        return self.client.get(
            f"{self._BASE_PATH}/pipelines/{job_id}/items",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def retry_pipeline_job(
        self,
        *,
        workroom_id: str,
        job_id: str,
        callback: Optional[dict[str, Any]] = None,
        idempotency_key: str | None = None,
        force: bool | None = None,
    ) -> dict[str, Any]:
        """Retry failed/incomplete items from a replayable source-import job.

        Maps to ``POST /context/pipelines/{job_id}/retry``.
        """
        payload: dict[str, Any] = {}
        if callback is not None:
            payload["callback"] = callback
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        if force is not None:
            payload["force"] = force
        return self.client.post(
            f"{self._BASE_PATH}/pipelines/{job_id}/retry",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def rerun_pipeline_job(
        self,
        *,
        workroom_id: str,
        job_id: str,
        callback: Optional[dict[str, Any]] = None,
        idempotency_key: str | None = None,
        force: bool | None = None,
    ) -> dict[str, Any]:
        """Rerun all recorded source descriptors from a prior source-import job.

        Maps to ``POST /context/pipelines/{job_id}/rerun``.
        """
        payload: dict[str, Any] = {}
        if callback is not None:
            payload["callback"] = callback
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        if force is not None:
            payload["force"] = force
        return self.client.post(
            f"{self._BASE_PATH}/pipelines/{job_id}/rerun",
            json=payload,
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
        vectordb_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query, "top_k": top_k}
        if collection_name is not None:
            payload["collection_name"] = collection_name
        if score_threshold is not None:
            payload["score_threshold"] = score_threshold
        if vectordb_id is not None:
            payload["vectordb_id"] = vectordb_id
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
        vectordb_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "score_threshold": score_threshold,
        }
        if collection_names is not None:
            payload["collection_names"] = collection_names
        if vectordb_id is not None:
            payload["vectordb_id"] = vectordb_id
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
        collection_name: str | None = None,
        top_k: int = 10,
        score_threshold: float | None = None,
        vectordb_id: str | None = None,
        max_iterations: int = 1,
        relevance_threshold: float = 0.7,
        enable_graph_search: bool = False,
        ontology_id: str | None = None,
        group_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run agentic search with LLM synthesis over a workroom's collections.

        Targets the canonical unified search endpoint with ``synthesize=True``.
        The legacy ``/context/search/agentic`` route is deprecated server-side
        in favour of ``/context/search/unified``; this method wraps the latter so
        callers stay on the supported path and receive the unified response shape
        (``results``/``sources``/``synthesis``/``citations``). Set
        ``max_iterations`` > 1 for iterative query refinement, or
        ``enable_graph_search`` with ``ontology_id``/``group_ids`` to fold
        knowledge-graph results into the synthesis. ``ontology_id`` and
        ``group_ids`` only take effect when ``enable_graph_search`` is True; the
        server ignores them otherwise.
        """
        payload: dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "synthesize": True,
            "max_iterations": max_iterations,
            "relevance_threshold": relevance_threshold,
            "enable_graph_search": enable_graph_search,
        }
        if collection_name is not None:
            payload["collection_name"] = collection_name
        if score_threshold is not None:
            payload["score_threshold"] = score_threshold
        if vectordb_id is not None:
            payload["vectordb_id"] = vectordb_id
        if ontology_id is not None:
            payload["ontology_id"] = ontology_id
        if group_ids is not None:
            payload["group_ids"] = group_ids
        return self.client.post(
            f"{self._BASE_PATH}/search/unified",
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

    # Raw-file object storage CRUD

    def store_raw_file(
        self,
        *,
        workroom_id: str,
        filename: str,
        content: bytes | str,
        content_type: str | None = None,
        source_urn: str | None = None,
        source_kind: str | None = None,
        source_ref: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Store a raw file directly into workroom-scoped object storage.

        ``content`` may be raw ``bytes`` or a ``str`` (UTF-8 encoded before
        base64). The server expects the payload base64-encoded on the wire;
        this method performs that encoding so callers pass plain content.

        Args:
            workroom_id: Owning workroom (sent as ``X-Workroom-ID``).
            filename: Original filename for the stored raw file.
            content: Raw file bytes (or a UTF-8 string).
            content_type: Optional MIME type; the server guesses from the
                filename when omitted.
            source_urn: Optional source URN (``inline://`` / ``workspace://``
                schemes only for inline create).
            source_kind: Optional source mode (``inline`` or ``workspace``).
            source_ref: Optional connector/source reference metadata.
            metadata: Optional caller-supplied metadata to persist.

        Returns:
            The stored raw-file detail record.
        """
        raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
        payload: dict[str, Any] = {
            "filename": filename,
            "content_base64": base64.b64encode(raw_bytes).decode("ascii"),
        }
        if content_type is not None:
            payload["content_type"] = content_type
        if source_urn is not None:
            payload["source_urn"] = source_urn
        if source_kind is not None:
            payload["source_kind"] = source_kind
        if source_ref is not None:
            payload["source_ref"] = source_ref
        if metadata is not None:
            payload["metadata"] = metadata
        return self.client.post(
            f"{self._BASE_PATH}/storage/raw",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def list_raw_files(
        self,
        *,
        workroom_id: str,
        source_urn: str | None = None,
        job_id: str | None = None,
        connector_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_markings: bool = False,
    ) -> dict[str, Any]:
        """List raw files stored for a workroom.

        Returns the server's ``{"items": [...], "count": N}`` response. Set
        ``include_markings=True`` to attach aggregated security markings to
        each row.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if source_urn is not None:
            params["source_urn"] = source_urn
        if job_id is not None:
            params["job_id"] = job_id
        if connector_id is not None:
            params["connector_id"] = connector_id
        if include_markings:
            params["include_markings"] = include_markings
        return self.client.get(
            f"{self._BASE_PATH}/storage/raw",
            params=params,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def get_raw_file(
        self,
        file_id: str,
        *,
        workroom_id: str,
        include_download_url: bool = False,
        expires_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Get one raw-file metadata record by ID and workroom scope.

        Set ``include_download_url=True`` to request a temporary presigned
        download URL (only populated when S3 metadata exists);
        ``expires_seconds`` overrides the presigned URL TTL (30-3600s).
        """
        params: dict[str, Any] = {}
        if include_download_url:
            params["include_download_url"] = include_download_url
        if expires_seconds is not None:
            params["expires_seconds"] = expires_seconds
        return self.client.get(
            f"{self._BASE_PATH}/storage/raw/{file_id}",
            params=params or None,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def update_raw_file(
        self,
        file_id: str,
        *,
        workroom_id: str,
        content: str,
        if_match: str | None = None,
    ) -> dict[str, Any]:
        """Edit the plain-text content of a raw file.

        ``content`` is sent verbatim as the new file body (the server rejects
        empty/whitespace-only content and enforces a UTF-8 byte cap). Pass the
        file's ``updated_at`` token from a prior response as ``if_match`` for
        optimistic concurrency control — a stale token returns HTTP 409 with
        the current token in the response detail.
        """
        headers = self._merge_headers(workroom_id=workroom_id)
        if if_match is not None:
            headers["If-Match"] = if_match
        return self.client.put(
            f"{self._BASE_PATH}/storage/raw/{file_id}",
            json={"content": content},
            headers=headers,
        )

    # OmniParse instance lifecycle CRUD

    def list_omniparses(
        self,
        *,
        workroom_id: str,
    ) -> list[dict[str, Any]]:
        """List OmniParse runtime instances for a workroom."""
        return self.client.get(
            f"{self._BASE_PATH}/omniparses",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def get_omniparse(
        self,
        omniparse_id: str,
        *,
        workroom_id: str,
    ) -> dict[str, Any]:
        """Get one OmniParse instance by ID within a workroom scope."""
        return self.client.get(
            f"{self._BASE_PATH}/omniparses/{omniparse_id}",
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def create_omniparse(
        self,
        *,
        name: str,
        workroom_id: str,
        template_name: str = "tool-omniparse",
        config: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Provision an OmniParse runtime instance.

        ``name`` is the instance name; ``template_name`` selects the App Garden
        tool template (defaults to ``tool-omniparse``); ``config`` carries
        optional OmniParse environment configuration.
        """
        payload: dict[str, Any] = {
            "name": name,
            "template_name": template_name,
        }
        if config is not None:
            payload["config"] = config
        return self.client.post(
            f"{self._BASE_PATH}/omniparses",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def update_omniparse(
        self,
        omniparse_id: str,
        *,
        workroom_id: str,
        config: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Update an OmniParse instance's environment configuration."""
        payload: dict[str, Any] = {}
        if config is not None:
            payload["config"] = config
        return self.client.put(
            f"{self._BASE_PATH}/omniparses/{omniparse_id}",
            json=payload,
            headers=self._merge_headers(workroom_id=workroom_id),
        )

    def delete_omniparse(
        self,
        omniparse_id: str,
        *,
        workroom_id: str,
    ) -> dict[str, Any]:
        """Delete an OmniParse runtime instance."""
        return self.client.delete(
            f"{self._BASE_PATH}/omniparses/{omniparse_id}",
            headers=self._merge_headers(workroom_id=workroom_id),
        )
