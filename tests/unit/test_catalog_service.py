from __future__ import annotations

import pytest

from kamiwaza_sdk.schemas.catalog import ContainerCreate, DatasetCreate, SecretCreate
from kamiwaza_sdk.services.catalog import CatalogService, ContainerClient, DatasetClient, SecretClient

pytestmark = pytest.mark.unit


def test_catalog_service_create_dataset_roundtrip(dummy_client):
    dataset_response = {
        "urn": "urn:li:dataset:(s3,my,PROD)",
        "name": "/tmp/data",
        "platform": "s3",
        "environment": "PROD",
        "tags": [],
        "properties": {},
    }
    canned = {
        ("post", "/catalog/datasets/"): dataset_response["urn"],
        ("get", "/catalog/datasets/by-urn"): dataset_response,
    }
    client = dummy_client(canned)
    service = CatalogService(client)

    dataset = service.create_dataset(dataset_name="/tmp/data", platform="s3")

    assert dataset.urn == dataset_response["urn"]
    assert client.calls[0][1] == "/catalog/datasets/"
    assert client.calls[1][1] == "/catalog/datasets/by-urn"


def test_container_membership_helpers_use_query_endpoints(dummy_client):
    responses = {
        ("post", "/catalog/containers/"): "urn:li:container:1",
        ("post", "/catalog/containers/by-urn/datasets"): {"message": "ok"},
        ("delete", "/catalog/containers/by-urn/datasets"): {"message": "removed"},
    }
    client = dummy_client(responses)
    containers = ContainerClient(client)

    containers.create(ContainerCreate(name="demo"))
    containers.add_dataset("container", "dataset")
    containers.remove_dataset("container", "dataset")

    _, add_path, add_kwargs = client.calls[1]
    assert add_path == "/catalog/containers/by-urn/datasets"
    assert add_kwargs["params"]["container_urn"] == "container"
    assert add_kwargs["json"]["dataset_urn"] == "dataset"


def test_secret_client_sets_clobber_flag(dummy_client):
    responses = {
        ("post", "/catalog/secrets/"): "urn:li:secret:demo",
        ("get", "/catalog/secrets/by-urn"): {
            "urn": "urn:li:secret:demo",
            "name": "demo",
            "owner": "urn:li:corpuser:demo",
        },
    }
    client = dummy_client(responses)
    secrets = SecretClient(client)

    urn = secrets.create(
        SecretCreate(name="demo", value="hunter2", owner="urn:li:corpuser:demo"),
        clobber=True,
    )
    assert urn == "urn:li:secret:demo"
    method, path, kwargs = client.calls[0]
    assert kwargs["params"]["clobber"] == "true"
    assert kwargs["json"]["value"] == "hunter2"


def test_dataset_client_encode_helper():
    encoded = DatasetClient.encode_path_urn("urn:li:dataset:(s3,my path,PROD)")
    assert "%2F" in encoded or "%28" in encoded
