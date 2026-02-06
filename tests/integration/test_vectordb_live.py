"""Integration tests for TS21 VECTORDB endpoints.

Tests cover:
- TS21.001: GET /vectordb/ - List vectordb instances
- TS21.002: POST /vectordb/ - Create vectordb instance
- TS21.003: GET /vectordb/collections - List collections
- TS21.004: DELETE /vectordb/collections/{collection_name} - Drop collection
- TS21.005: POST /vectordb/insert_vectors - Insert vectors
- TS21.006: POST /vectordb/search_vectors - Search vectors
- TS21.007: DELETE /vectordb/{vectordb_id} - Remove vectordb instance
- TS21.008: GET /vectordb/{vectordb_id} - Get vectordb by ID

Note: Vector operations (TS21.003-006) require a running Milvus instance
and registered vectordb. Tests will skip if not available.
"""
from __future__ import annotations

import pytest
from uuid import uuid4

from kamiwaza_sdk.exceptions import APIError, VectorDBUnavailableError
from kamiwaza_sdk.schemas.vectordb import (
    CreateVectorDB,
    InsertVectorsRequest,
    SearchVectorsRequest,
)

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


class TestVectorDBListOperations:
    """Tests for list operations - always available."""

    def test_list_vectordbs(self, live_kamiwaza_client) -> None:
        """TS21.001: GET /vectordb/ - List all vectordb instances."""
        vectordbs = live_kamiwaza_client.vectordb.get_vectordbs()
        assert isinstance(vectordbs, list)
        # May be empty but should return a list
        for vdb in vectordbs:
            assert hasattr(vdb, "id")
            assert hasattr(vdb, "name")
            assert hasattr(vdb, "engine")

    def test_list_vectordbs_filter_by_engine(self, live_kamiwaza_client) -> None:
        """TS21.001: GET /vectordb/ - Test engine filter parameter."""
        # Test with a filter - even if no results, should not error
        vectordbs = live_kamiwaza_client.vectordb.get_vectordbs(engine="milvus")
        assert isinstance(vectordbs, list)


class TestVectorDBLifecycle:
    """Tests for vectordb CRUD operations."""

    def test_create_get_and_remove_vectordb(self, live_kamiwaza_client) -> None:
        """TS21.002 + TS21.008 + TS21.007: Create, get, and remove vectordb."""
        created = None
        try:
            # TS21.002: Create vectordb
            create_payload = CreateVectorDB(
                name="sdk-test-vectordb",
                engine="milvus",
                description="Test vectordb created by SDK integration tests",
                host="localhost",
                port=19530
            )
            created = live_kamiwaza_client.vectordb.create_vectordb(create_payload)
            assert created is not None
            assert created.name == "sdk-test-vectordb"
            assert created.engine == "milvus"
            assert created.id is not None

            # TS21.008: Get vectordb by ID
            retrieved = live_kamiwaza_client.vectordb.get_vectordb(str(created.id))
            assert retrieved is not None
            assert retrieved.id == created.id
            assert retrieved.name == "sdk-test-vectordb"

            # Verify it appears in list
            all_vdbs = live_kamiwaza_client.vectordb.get_vectordbs()
            assert any(vdb.id == created.id for vdb in all_vdbs)

        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for vectordb operations")
            raise
        finally:
            # TS21.007: Remove vectordb
            if created:
                try:
                    result = live_kamiwaza_client.vectordb.remove_vectordb(str(created.id))
                    assert result is not None
                except APIError:
                    pass  # Best effort cleanup

    def test_get_nonexistent_vectordb(self, live_kamiwaza_client) -> None:
        """TS21.008: GET /vectordb/{vectordb_id} - Test with non-existent ID."""
        fake_id = str(uuid4())

        try:
            live_kamiwaza_client.vectordb.get_vectordb(fake_id)
            pytest.fail("Expected error for non-existent vectordb")
        except APIError as exc:
            # Should get 404 or 500 for non-existent vectordb
            assert exc.status_code in (404, 500, 422)
        except Exception:
            # Other errors acceptable
            pass

    def test_remove_nonexistent_vectordb(self, live_kamiwaza_client) -> None:
        """TS21.007: DELETE /vectordb/{vectordb_id} - Test with non-existent ID."""
        fake_id = str(uuid4())

        try:
            live_kamiwaza_client.vectordb.remove_vectordb(fake_id)
            pytest.fail("Expected error for non-existent vectordb")
        except APIError as exc:
            # Should get 404 or 500 for non-existent vectordb
            assert exc.status_code in (404, 500, 422)
        except Exception:
            # Other errors acceptable
            pass


class TestVectorDBCollectionOperations:
    """Tests for collection operations - requires running Milvus."""

    @pytest.fixture
    def registered_vectordb(self, live_kamiwaza_client):
        """Get or create a registered vectordb for testing."""
        # First check if there's already a registered vectordb
        vectordbs = live_kamiwaza_client.vectordb.get_vectordbs()
        if vectordbs:
            return vectordbs[0]

        # Otherwise skip - we don't want to create fake connections
        pytest.skip("No vectordb instances registered")

    def test_list_collections(self, live_kamiwaza_client, registered_vectordb) -> None:
        """TS21.003: GET /vectordb/collections - List collections."""
        try:
            collections = live_kamiwaza_client.vectordb.list_collections()
            assert isinstance(collections, list)
            # Collections may be empty but should return a list
        except APIError as exc:
            if exc.status_code == 500:
                pytest.skip(f"Collection listing failed - Milvus may not be running: {exc}")
            raise

    def test_drop_nonexistent_collection(self, live_kamiwaza_client, registered_vectordb) -> None:
        """TS21.004: DELETE /vectordb/collections/{name} - Test with non-existent collection."""
        try:
            # Try to drop a collection that doesn't exist
            result = live_kamiwaza_client.vectordb.drop_collection("nonexistent_sdk_test_collection")
            # Some systems return success even for non-existent collections
            assert result is not None
        except APIError as exc:
            # Should get 404 or 500 for non-existent collection
            if exc.status_code == 500:
                pytest.skip(f"Milvus may not be running: {exc}")
            # 404 is acceptable for non-existent collection
            assert exc.status_code in (404, 500, 422)


class TestVectorOperations:
    """Tests for vector insert and search operations.

    Note: These require a running Milvus instance and may create test collections.
    """

    @pytest.fixture
    def registered_vectordb(self, live_kamiwaza_client):
        """Get a registered vectordb or skip."""
        vectordbs = live_kamiwaza_client.vectordb.get_vectordbs()
        if vectordbs:
            return vectordbs[0]
        pytest.skip("No vectordb instances registered - cannot test vector operations")

    def test_insert_and_search_vectors(self, live_kamiwaza_client, registered_vectordb) -> None:
        """TS21.005 + TS21.006: Insert vectors and search."""
        collection_name = "sdk_test_collection"

        try:
            # TS21.005: Insert vectors
            # Create simple test vectors (3-dimensional for testing)
            test_vectors = [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ]
            test_metadata = [
                {"source": "test1", "offset": 0, "model_name": "test", "catalog_urn": "", "filename": "file1.txt"},
                {"source": "test2", "offset": 1, "model_name": "test", "catalog_urn": "", "filename": "file2.txt"},
                {"source": "test3", "offset": 2, "model_name": "test", "catalog_urn": "", "filename": "file3.txt"},
            ]

            insert_request = InsertVectorsRequest(
                collection_name=collection_name,
                vectors=test_vectors,
                metadata=test_metadata,
                dimensions=3,
                field_list=[
                    ("source", "str"),
                    ("offset", "int"),
                    ("model_name", "str"),
                    ("catalog_urn", "str"),
                    ("filename", "str"),
                ]
            )

            insert_result = live_kamiwaza_client.vectordb.insert_vectors(insert_request)
            assert insert_result is not None
            assert hasattr(insert_result, "rows_inserted")
            assert insert_result.rows_inserted == 3

            # TS21.006: Search vectors
            search_request = SearchVectorsRequest(
                collection_name=collection_name,
                query_vectors=[[1.0, 0.0, 0.0]],  # Should match first vector
                limit=2,
                output_fields=["source", "offset"]
            )

            search_results = live_kamiwaza_client.vectordb.search_vectors(search_request)
            assert isinstance(search_results, list)
            if search_results:
                result = search_results[0]
                assert hasattr(result, "id")
                assert hasattr(result, "score")
                assert hasattr(result, "metadata")

        except VectorDBUnavailableError as exc:
            pytest.skip(f"VectorDB backend is not configured: {exc}")
        except APIError as exc:
            if exc.status_code == 500:
                pytest.skip(f"Vector operations failed - Milvus may not be running: {exc}")
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for vector operations")
            raise
        finally:
            # Cleanup: Drop the test collection
            try:
                live_kamiwaza_client.vectordb.drop_collection(collection_name)
            except (APIError, Exception):
                pass  # Best effort cleanup

    def test_search_with_no_results(self, live_kamiwaza_client, registered_vectordb) -> None:
        """TS21.006: POST /vectordb/search_vectors - Search in non-existent collection."""
        try:
            search_request = SearchVectorsRequest(
                collection_name="nonexistent_collection",
                query_vectors=[[1.0, 0.0, 0.0]],
                limit=5
            )

            # Should either return empty or raise error
            results = live_kamiwaza_client.vectordb.search_vectors(search_request)
            assert isinstance(results, list)
        except VectorDBUnavailableError as exc:
            pytest.skip(f"VectorDB backend is not configured: {exc}")
        except APIError as exc:
            if exc.status_code == 500:
                pytest.skip(f"Milvus may not be running: {exc}")
            # 404, 501 (VectorDB unavailable), or validation error acceptable
            assert exc.status_code in (404, 500, 501, 422, 400)


class TestVectorDBHelperMethods:
    """Tests for simplified helper methods in the SDK."""

    @pytest.fixture
    def registered_vectordb(self, live_kamiwaza_client):
        """Get a registered vectordb or skip."""
        vectordbs = live_kamiwaza_client.vectordb.get_vectordbs()
        if vectordbs:
            return vectordbs[0]
        pytest.skip("No vectordb instances registered")

    def test_insert_helper_method(self, live_kamiwaza_client, registered_vectordb) -> None:
        """Test the simplified insert() helper method."""
        collection_name = "sdk_test_helper_collection"

        try:
            # Use the helper method
            result = live_kamiwaza_client.vectordb.insert(
                vectors=[[1.0, 0.5, 0.0], [0.0, 1.0, 0.5]],
                metadata=[
                    {"source": "helper_test1", "filename": "helper1.txt"},
                    {"source": "helper_test2", "filename": "helper2.txt"},
                ],
                collection_name=collection_name
            )
            assert result is not None
            assert result.rows_inserted == 2

        except VectorDBUnavailableError as exc:
            pytest.skip(f"VectorDB backend is not configured: {exc}")
        except APIError as exc:
            if exc.status_code == 500:
                pytest.skip(f"Insert failed - Milvus may not be running: {exc}")
            raise
        finally:
            try:
                live_kamiwaza_client.vectordb.drop_collection(collection_name)
            except (APIError, Exception):
                pass

    def test_search_helper_method(self, live_kamiwaza_client, registered_vectordb) -> None:
        """Test the simplified search() helper method."""
        collection_name = "sdk_test_search_helper_collection"

        try:
            # First insert some vectors
            live_kamiwaza_client.vectordb.insert(
                vectors=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                metadata=[
                    {"source": "search_test1", "filename": "s1.txt"},
                    {"source": "search_test2", "filename": "s2.txt"},
                ],
                collection_name=collection_name
            )

            # Use the search helper
            results = live_kamiwaza_client.vectordb.search(
                query_vector=[1.0, 0.0, 0.0],
                collection_name=collection_name,
                limit=2
            )
            assert isinstance(results, list)
            # Results may be empty if Milvus isn't properly configured

        except VectorDBUnavailableError as exc:
            pytest.skip(f"VectorDB backend is not configured: {exc}")
        except APIError as exc:
            if exc.status_code == 500:
                pytest.skip(f"Search failed - Milvus may not be running: {exc}")
            raise
        finally:
            try:
                live_kamiwaza_client.vectordb.drop_collection(collection_name)
            except (APIError, VectorDBUnavailableError, Exception):
                pass
