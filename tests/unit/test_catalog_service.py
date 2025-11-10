from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kamiwaza_sdk.schemas.catalog import ContainerCreate, DatasetCreate, SecretCreate
from kamiwaza_sdk.services.catalog import CatalogService, ContainerClient, DatasetClient, SecretClient


class DummyClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, path: str, **kwargs):
        self.calls.append(("post", path, kwargs))
        return self.responses[("post", path)]

    def get(self, path: str, **kwargs):
        self.calls.append(("get", path, kwargs))
        return self.responses[("get", path)]

    def patch(self, path: str, **kwargs):
        self.calls.append(("patch", path, kwargs))
        return self.responses[("patch", path)]

    def delete(self, path: str, **kwargs):
        self.calls.append(("delete", path, kwargs))
        return self.responses[("delete", path)]


def test_catalog_service_create_dataset_roundtrip():
    dataset_response = {
        "urn": "urn:li:dataset:(s3,my,PROD)",
        "name": "/tmp/data",
        "platform": "s3",
        "environment": "PROD",
        "tags": [],
        "properties": {},
    }
    responses = {
        ("post", "/catalog/datasets/"): dataset_response["urn"],
        ("get", "/catalog/datasets/by-urn"): dataset_response,
    }
    client = DummyClient(responses)
    service = CatalogService(client)

    dataset = service.create_dataset(dataset_name="/tmp/data", platform="s3")

    assert dataset.urn == dataset_response["urn"]
    assert client.calls[0][1] == "/catalog/datasets/"
    assert client.calls[1][1] == "/catalog/datasets/by-urn"


def test_container_membership_helpers_use_query_endpoints():
    responses = {
        ("post", "/catalog/containers/"): "urn:li:container:1",
        ("post", "/catalog/containers/by-urn/datasets"): {"message": "ok"},
        ("delete", "/catalog/containers/by-urn/datasets"): {"message": "removed"},
    }
    client = DummyClient(responses)
    containers = ContainerClient(client)

    containers.create(ContainerCreate(name="demo"))
    containers.add_dataset("container", "dataset")
    containers.remove_dataset("container", "dataset")

    _, add_path, add_kwargs = client.calls[1]
    assert add_path == "/catalog/containers/by-urn/datasets"
    assert add_kwargs["params"]["container_urn"] == "container"
    assert add_kwargs["json"]["dataset_urn"] == "dataset"


def test_secret_client_sets_clobber_flag():
    responses = {
        ("post", "/catalog/secrets/"): "urn:li:secret:demo",
        ("get", "/catalog/secrets/by-urn"): {
            "urn": "urn:li:secret:demo",
            "name": "demo",
            "owner": "urn:li:corpuser:demo",
        },
    }
    client = DummyClient(responses)
    secrets = SecretClient(client)

    urn = secrets.create(
        SecretCreate(name="demo", value="hunter2", owner="urn:li:corpuser:demo"),
        clobber=True,
    )
    assert urn == "urn:li:secret:demo"
    method, path, kwargs = client.calls[0]
    assert kwargs["params"]["clobber"] == "true"


def test_dataset_client_encode_helper():
    encoded = DatasetClient.encode_path_urn("urn:li:dataset:(s3,my path,PROD)")
    assert "%2F" in encoded or "%28" in encoded
