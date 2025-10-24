# tests/unit/services/test_catalog.py

import pytest
from unittest.mock import Mock
from kamiwaza_sdk.services.catalog import CatalogService
from kamiwaza_sdk.schemas.catalog import (
    Dataset,
    DatasetCreate,
    DatasetUpdate,
    Schema,
    SchemaField,
    Container,
    ContainerCreate,
    ContainerUpdate,
)


class TestCatalogServiceV1:
    """Tests for V1 API methods (legacy endpoints)."""

    @pytest.fixture
    def mock_client(self):
        """Create mock HTTP client."""
        return Mock()

    @pytest.fixture
    def service(self, mock_client):
        """Create catalog service with mocked client."""
        return CatalogService(mock_client)

    def test_list_datasets(self, service, mock_client):
        """Test listing datasets with V1 API."""
        # Arrange
        mock_client.get.return_value = [
            {
                "urn": "urn:li:dataset:(urn:li:dataPlatform:file,/data/test1.csv,PROD)",
                "name": "test1.csv",
                "platform": "file",
                "environment": "PROD",
                "description": "Test dataset 1",
                "tags": ["test"],
                "properties": {}
            },
            {
                "urn": "urn:li:dataset:(urn:li:dataPlatform:file,/data/test2.csv,PROD)",
                "name": "test2.csv",
                "platform": "file",
                "environment": "PROD",
                "description": "Test dataset 2",
                "tags": [],
                "properties": {}
            }
        ]

        # Act
        datasets = service.list_datasets()

        # Assert
        assert len(datasets) == 2
        assert all(isinstance(d, Dataset) for d in datasets)
        assert datasets[0].name == "test1.csv"
        assert datasets[1].name == "test2.csv"
        mock_client.get.assert_called_once_with("/catalog/datasets/", params={})

    def test_list_datasets_with_query(self, service, mock_client):
        """Test listing datasets with query filter."""
        # Arrange
        mock_client.get.return_value = []

        # Act
        service.list_datasets(query="test")

        # Assert
        mock_client.get.assert_called_once_with(
            "/catalog/datasets/",
            params={"query": "test"}
        )

    def test_create_dataset(self, service, mock_client):
        """Test creating a dataset with V1 API."""
        # Arrange
        mock_client.post.return_value = "urn:li:dataset:(urn:li:dataPlatform:file,/data/new.csv,PROD)"
        dataset_create = DatasetCreate(
            name="new.csv",
            platform="file",
            environment="PROD",
            description="New test dataset",
            tags=["new", "test"]
        )

        # Act
        urn = service.create_dataset(dataset_create)

        # Assert
        assert urn == "urn:li:dataset:(urn:li:dataPlatform:file,/data/new.csv,PROD)"
        mock_client.post.assert_called_once_with(
            "/catalog/datasets/",
            json=dataset_create.model_dump()
        )

    def test_list_containers(self, service, mock_client):
        """Test listing containers with V1 API."""
        # Arrange
        mock_client.get.return_value = [
            {
                "urn": "urn:li:container:(prod_db)",
                "name": "prod_db",
                "description": "Production database",
                "tags": ["prod"],
                "properties": {},
                "sub_containers": [],
                "datasets": []
            }
        ]

        # Act
        containers = service.list_containers()

        # Assert
        assert len(containers) == 1
        assert isinstance(containers[0], Container)
        assert containers[0].name == "prod_db"
        mock_client.get.assert_called_once_with("/catalog/containers/", params={})

    def test_create_container(self, service, mock_client):
        """Test creating a container with V1 API."""
        # Arrange
        mock_client.post.return_value = "urn:li:container:(new_container)"
        container_create = ContainerCreate(
            name="new_container",
            platform="file",
            description="New container"
        )

        # Act
        urn = service.create_container(container_create)

        # Assert
        assert urn == "urn:li:container:(new_container)"
        mock_client.post.assert_called_once_with(
            "/catalog/containers/",
            json=container_create.model_dump()
        )


class TestCatalogServiceV2Datasets:
    """Tests for V2 dataset API methods."""

    @pytest.fixture
    def mock_client(self):
        return Mock()

    @pytest.fixture
    def service(self, mock_client):
        return CatalogService(mock_client)

    @pytest.fixture
    def sample_dataset_urn(self):
        return "urn:li:dataset:(urn:li:dataPlatform:file,/var/tmp/docs,PROD)"

    def test_get_dataset_v2(self, service, mock_client, sample_dataset_urn):
        """Test getting dataset by URN with V2 API."""
        # Arrange
        mock_client.get.return_value = {
            "urn": sample_dataset_urn,
            "name": "docs",
            "platform": "file",
            "environment": "PROD",
            "description": "Documentation dataset",
            "tags": ["docs"],
            "properties": {}
        }

        # Act
        dataset = service.get_dataset_v2(sample_dataset_urn)

        # Assert
        assert isinstance(dataset, Dataset)
        assert dataset.urn == sample_dataset_urn
        assert dataset.name == "docs"
        mock_client.get.assert_called_once_with(
            f"/catalog/datasets/v2/{sample_dataset_urn}"
        )

    def test_update_dataset_v2(self, service, mock_client, sample_dataset_urn):
        """Test updating dataset with V2 API."""
        # Arrange
        update_data = DatasetUpdate(
            description="Updated description",
            tags=["docs", "updated"]
        )
        mock_client.patch.return_value = {
            "urn": sample_dataset_urn,
            "name": "docs",
            "platform": "file",
            "environment": "PROD",
            "description": "Updated description",
            "tags": ["docs", "updated"],
            "properties": {}
        }

        # Act
        dataset = service.update_dataset_v2(sample_dataset_urn, update_data)

        # Assert
        assert isinstance(dataset, Dataset)
        assert dataset.description == "Updated description"
        assert "updated" in dataset.tags
        mock_client.patch.assert_called_once_with(
            f"/catalog/datasets/v2/{sample_dataset_urn}",
            json=update_data.model_dump(exclude_unset=True)
        )

    def test_delete_dataset_v2(self, service, mock_client, sample_dataset_urn):
        """Test deleting dataset with V2 API."""
        # Arrange
        mock_client.delete.return_value = None

        # Act
        service.delete_dataset_v2(sample_dataset_urn)

        # Assert
        mock_client.delete.assert_called_once_with(
            f"/catalog/datasets/v2/{sample_dataset_urn}"
        )

    def test_get_dataset_schema_v2(self, service, mock_client, sample_dataset_urn):
        """Test getting dataset schema with V2 API."""
        # Arrange
        mock_client.get.return_value = {
            "name": "user_schema",
            "platform": "file",
            "version": 1,
            "fields": [
                {"name": "id", "type": "string", "description": "User ID"},
                {"name": "email", "type": "string", "description": "User email"}
            ]
        }

        # Act
        schema = service.get_dataset_schema_v2(sample_dataset_urn)

        # Assert
        assert isinstance(schema, Schema)
        assert schema.name == "user_schema"
        assert len(schema.fields) == 2
        assert all(isinstance(f, SchemaField) for f in schema.fields)
        assert schema.fields[0].name == "id"
        mock_client.get.assert_called_once_with(
            f"/catalog/datasets/v2/{sample_dataset_urn}/schema"
        )

    def test_update_dataset_schema_v2(self, service, mock_client, sample_dataset_urn):
        """Test updating dataset schema with V2 API."""
        # Arrange
        schema = Schema(
            name="updated_schema",
            platform="file",
            version=2,
            fields=[
                SchemaField(name="id", type="string"),
                SchemaField(name="name", type="string"),
                SchemaField(name="created_at", type="datetime")
            ]
        )
        mock_client.put.return_value = None

        # Act
        service.update_dataset_schema_v2(sample_dataset_urn, schema)

        # Assert
        mock_client.put.assert_called_once_with(
            f"/catalog/datasets/v2/{sample_dataset_urn}/schema",
            json=schema.model_dump()
        )


class TestCatalogServiceV2Containers:
    """Tests for V2 container API methods."""

    @pytest.fixture
    def mock_client(self):
        return Mock()

    @pytest.fixture
    def service(self, mock_client):
        return CatalogService(mock_client)

    @pytest.fixture
    def sample_container_urn(self):
        return "urn:li:container:(prod_db)"

    @pytest.fixture
    def sample_dataset_urn(self):
        return "urn:li:dataset:(urn:li:dataPlatform:file,/data/users.csv,PROD)"

    def test_get_container_v2(self, service, mock_client, sample_container_urn):
        """Test getting container by URN with V2 API."""
        # Arrange
        mock_client.get.return_value = {
            "urn": sample_container_urn,
            "name": "prod_db",
            "description": "Production database",
            "tags": ["prod"],
            "properties": {},
            "sub_containers": [],
            "datasets": ["urn:li:dataset:(...)"]
        }

        # Act
        container = service.get_container_v2(sample_container_urn)

        # Assert
        assert isinstance(container, Container)
        assert container.urn == sample_container_urn
        assert container.name == "prod_db"
        assert len(container.datasets) == 1
        mock_client.get.assert_called_once_with(
            f"/catalog/containers/v2/{sample_container_urn}"
        )

    def test_update_container_v2(self, service, mock_client, sample_container_urn):
        """Test updating container with V2 API."""
        # Arrange
        update_data = ContainerUpdate(
            description="Updated production database",
            tags=["prod", "updated"]
        )
        mock_client.patch.return_value = {
            "urn": sample_container_urn,
            "name": "prod_db",
            "description": "Updated production database",
            "tags": ["prod", "updated"],
            "properties": {},
            "sub_containers": [],
            "datasets": []
        }

        # Act
        container = service.update_container_v2(sample_container_urn, update_data)

        # Assert
        assert isinstance(container, Container)
        assert container.description == "Updated production database"
        assert "updated" in container.tags
        mock_client.patch.assert_called_once_with(
            f"/catalog/containers/v2/{sample_container_urn}",
            json=update_data.model_dump(exclude_unset=True)
        )

    def test_delete_container_v2(self, service, mock_client, sample_container_urn):
        """Test deleting container with V2 API."""
        # Arrange
        mock_client.delete.return_value = None

        # Act
        service.delete_container_v2(sample_container_urn)

        # Assert
        mock_client.delete.assert_called_once_with(
            f"/catalog/containers/v2/{sample_container_urn}"
        )

    def test_add_dataset_to_container_v2(
        self,
        service,
        mock_client,
        sample_container_urn,
        sample_dataset_urn
    ):
        """Test adding dataset to container with V2 API."""
        # Arrange
        mock_client.post.return_value = None

        # Act
        service.add_dataset_to_container_v2(sample_container_urn, sample_dataset_urn)

        # Assert
        mock_client.post.assert_called_once_with(
            f"/catalog/containers/v2/{sample_container_urn}/datasets",
            json={"dataset_urn": sample_dataset_urn}
        )

    def test_remove_dataset_from_container_v2(
        self,
        service,
        mock_client,
        sample_container_urn,
        sample_dataset_urn
    ):
        """Test removing dataset from container with V2 API."""
        # Arrange
        mock_client.delete.return_value = None

        # Act
        service.remove_dataset_from_container_v2(
            sample_container_urn,
            sample_dataset_urn
        )

        # Assert
        mock_client.delete.assert_called_once_with(
            f"/catalog/containers/v2/{sample_container_urn}/datasets/{sample_dataset_urn}"
        )


class TestCatalogServiceUtilities:
    """Tests for utility methods."""

    @pytest.fixture
    def mock_client(self):
        return Mock()

    @pytest.fixture
    def service(self, mock_client):
        return CatalogService(mock_client)

    def test_flush_catalog(self, service, mock_client):
        """Test flushing the entire catalog."""
        # Arrange
        mock_client.delete.return_value = None

        # Act
        service.flush_catalog()

        # Assert
        mock_client.delete.assert_called_once_with("/catalog/flush")
