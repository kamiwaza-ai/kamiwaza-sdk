from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.catalog import ContainerCreate, SecretCreate
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
    expected_urn = "urn:li:dataHubSecret:demo"
    responses = {
        ("post", "/catalog/secrets/"): expected_urn,
        ("get", "/catalog/secrets/v2/urn:li:dataHubSecret:demo"): {
            "urn": expected_urn,
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
    assert urn == expected_urn
    method, path, kwargs = client.calls[0]
    assert kwargs["params"]["clobber"] == "true"
    assert kwargs["json"]["value"] == "hunter2"


def test_secret_client_preserves_opaque_urn(dummy_client):
    expected_urn = "urn:li:dataHubSecret:demo"
    raw = expected_urn
    responses = {
        ("post", "/catalog/secrets/"): {"urn": expected_urn},
        ("get", f"/catalog/secrets/v2/{raw}"): {
            "urn": expected_urn,
            "name": "demo",
            "owner": "urn:li:corpuser:demo",
        },
        ("delete", f"/catalog/secrets/v2/{raw}"): {},
    }
    client = dummy_client(responses)
    secrets = SecretClient(client)

    urn = secrets.create(
        SecretCreate(name="demo", value="hunter2", owner="urn:li:corpuser:demo"),
        clobber=False,
    )
    secrets.get(urn)
    secrets.delete(urn)

    assert urn == expected_urn
    assert client.calls[1][1].endswith(raw)
    assert "params" not in client.calls[1][2]
    assert client.calls[2][1].endswith(raw)
    assert "params" not in client.calls[2][2]


def test_dataset_client_encode_helper():
    encoded = DatasetClient.encode_path_urn("urn:li:dataset:(s3,my path,PROD)")
    assert "%2F" in encoded or "%28" in encoded


def test_catalog_service_normalizes_path_to_location(dummy_client):
    dataset_urn = "urn:li:dataset:(s3,my,PROD)"
    dataset_response = {
        "urn": dataset_urn,
        "name": "demo",
        "platform": "s3",
        "environment": "PROD",
        "tags": [],
        "properties": {"path": "s3://bucket/key"},
    }
    responses = {
        ("get", "/catalog/datasets/by-urn"): dataset_response,
    }
    client = dummy_client(responses)
    service = CatalogService(client)

    dataset = service.get_dataset(dataset_urn)

    assert dataset.properties["location"] == "s3://bucket/key"
    assert "location" not in dataset_response["properties"], "should not mutate source dict"


def test_catalog_service_normalizes_location_to_path(dummy_client):
    dataset_urn = "urn:li:dataset:(s3,my,PROD)"
    raw_properties = {"location": "s3://bucket/key"}
    list_payload = [
        {
            "urn": dataset_urn,
            "name": "demo",
            "platform": "s3",
            "environment": "PROD",
            "tags": [],
            "properties": raw_properties,
        }
    ]
    responses = {
        ("get", "/catalog/datasets/"): list_payload,
    }
    client = dummy_client(responses)
    service = CatalogService(client)

    datasets = service.list_datasets()

    assert datasets[0].properties["path"] == "s3://bucket/key"
    assert "path" not in raw_properties, "original properties dict should remain untouched"


def test_dataset_client_list_merges_recent_and_list_all_after_query_500():
    class QueryFallbackClient:
        def __init__(self):
            self._recent_datasets = {
                "urn:li:dataset:(file,sdk-dataset-1,PROD)": 1.0,
            }
            self.calls = []

        def get(self, path, **kwargs):
            self.calls.append((path, kwargs))
            if kwargs.get("params") == {"query": "sdk-dataset"}:
                raise APIError("DataHub search failed", status_code=500)
            if kwargs.get("params") == {
                "urn": "urn:li:dataset:(file,sdk-dataset-1,PROD)"
            }:
                return {
                    "urn": "urn:li:dataset:(file,sdk-dataset-1,PROD)",
                    "name": "sdk-dataset-1",
                    "platform": "file",
                    "environment": "PROD",
                    "tags": [],
                    "properties": {},
                }
            if path == "/catalog/datasets/":
                return [
                    {
                        "urn": "urn:li:dataset:(file,sdk-dataset-old,PROD)",
                        "name": "sdk-dataset-old",
                        "platform": "file",
                        "environment": "PROD",
                        "tags": [],
                        "properties": {},
                    },
                    {
                        "urn": "urn:li:dataset:(file,sdk-dataset-1,PROD)",
                        "name": "sdk-dataset-1",
                        "platform": "file",
                        "environment": "PROD",
                        "tags": [],
                        "properties": {},
                    },
                ]
            raise AssertionError(f"Unexpected call: {path} {kwargs}")

    client = QueryFallbackClient()
    with pytest.warns(RuntimeWarning, match="Dataset query failed"):
        datasets = DatasetClient(client).list(query="sdk-dataset")

    assert [dataset.name for dataset in datasets] == ["sdk-dataset-old", "sdk-dataset-1"]
    assert client.calls == [
        ("/catalog/datasets/", {"params": {"query": "sdk-dataset"}}),
        (
            "/catalog/datasets/by-urn",
            {"params": {"urn": "urn:li:dataset:(file,sdk-dataset-1,PROD)"}},
        ),
        ("/catalog/datasets/", {}),
    ]


def test_dataset_client_list_filters_list_all_fallback_after_query_500():
    class QueryFallbackClient:
        def __init__(self):
            self._recent_datasets = {}
            self.calls = []

        def get(self, path, **kwargs):
            self.calls.append((path, kwargs))
            if kwargs.get("params") == {"query": "wanted"}:
                raise APIError("DataHub search failed", status_code=500)
            if path == "/catalog/datasets/":
                return [
                    {
                        "urn": "urn:li:dataset:(file,wanted-dataset,PROD)",
                        "name": "db-table",
                        "platform": "file",
                        "environment": "PROD",
                        "tags": [],
                        "properties": {},
                    },
                    {
                        "urn": "urn:li:dataset:(file,other-dataset,PROD)",
                        "name": "other",
                        "platform": "file",
                        "environment": "PROD",
                        "tags": [],
                        "properties": {},
                    },
                ]
            raise AssertionError(f"Unexpected call: {path} {kwargs}")

    client = QueryFallbackClient()
    with pytest.warns(RuntimeWarning, match="Dataset query failed"):
        datasets = DatasetClient(client).list(query="wanted")

    assert [dataset.urn for dataset in datasets] == [
        "urn:li:dataset:(file,wanted-dataset,PROD)"
    ]
    assert client.calls == [
        ("/catalog/datasets/", {"params": {"query": "wanted"}}),
        ("/catalog/datasets/", {}),
    ]


def test_secret_client_list_merges_recent_and_list_all_after_query_500():
    class QueryFallbackClient:
        def __init__(self):
            self._recent_secrets = {"urn:li:dataHubSecret:sdk-secret-1": 1.0}
            self.calls = []

        def get(self, path, **kwargs):
            self.calls.append((path, kwargs))
            if kwargs.get("params") == {"query": "sdk-secret"}:
                raise APIError("DataHub search failed", status_code=500)
            if path == "/catalog/secrets/v2/urn:li:dataHubSecret:sdk-secret-1":
                return {
                    "urn": "urn:li:dataHubSecret:sdk-secret-1",
                    "name": "sdk-secret-1",
                    "owner": "urn:li:corpuser:demo",
                }
            if path == "/catalog/secrets/":
                return [
                    {
                        "urn": "urn:li:dataHubSecret:sdk-secret-old",
                        "name": "sdk-secret-old",
                        "owner": "urn:li:corpuser:demo",
                    },
                    {
                        "urn": "urn:li:dataHubSecret:sdk-secret-1",
                        "name": "sdk-secret-1",
                        "owner": "urn:li:corpuser:demo",
                    },
                ]
            raise AssertionError(f"Unexpected call: {path} {kwargs}")

    client = QueryFallbackClient()
    with pytest.warns(RuntimeWarning, match="Secret query failed"):
        secrets = SecretClient(client).list(query="sdk-secret")

    assert [secret.name for secret in secrets] == ["sdk-secret-old", "sdk-secret-1"]
    assert client.calls == [
        ("/catalog/secrets/", {"params": {"query": "sdk-secret"}}),
        ("/catalog/secrets/v2/urn:li:dataHubSecret:sdk-secret-1", {}),
        ("/catalog/secrets/", {}),
    ]


def test_secret_client_list_filters_list_all_fallback_after_query_500():
    class QueryFallbackClient:
        def __init__(self):
            self._recent_secrets = {}
            self.calls = []

        def get(self, path, **kwargs):
            self.calls.append((path, kwargs))
            if kwargs.get("params") == {"query": "wanted"}:
                raise APIError("DataHub search failed", status_code=500)
            if path == "/catalog/secrets/":
                return [
                    {
                        "urn": "urn:li:dataHubSecret:wanted-secret",
                        "name": "db-password",
                        "owner": "urn:li:corpuser:demo",
                    },
                    {
                        "urn": "urn:li:dataHubSecret:other-secret",
                        "name": "other",
                        "owner": "urn:li:corpuser:demo",
                    },
                ]
            raise AssertionError(f"Unexpected call: {path} {kwargs}")

    client = QueryFallbackClient()
    with pytest.warns(RuntimeWarning, match="Secret query failed"):
        secrets = SecretClient(client).list(query="wanted")

    assert [secret.urn for secret in secrets] == ["urn:li:dataHubSecret:wanted-secret"]
    assert client.calls == [
        ("/catalog/secrets/", {"params": {"query": "wanted"}}),
        ("/catalog/secrets/", {}),
    ]


@pytest.mark.parametrize("client_cls,path", [(DatasetClient, "/catalog/datasets/"), (SecretClient, "/catalog/secrets/")])
def test_catalog_query_fallback_reraises_non_500_errors(client_cls, path):
    class QueryFallbackClient:
        def get(self, request_path, **kwargs):
            assert request_path == path
            assert kwargs == {"params": {"query": "wanted"}}
            raise APIError("Forbidden", status_code=403)

    with pytest.raises(APIError) as exc_info:
        client_cls(QueryFallbackClient()).list(query="wanted")

    assert exc_info.value.status_code == 403


@pytest.mark.parametrize(
    "client_cls,path,recent_attr",
    [
        (DatasetClient, "/catalog/datasets/", "_recent_datasets"),
        (SecretClient, "/catalog/secrets/", "_recent_secrets"),
    ],
)
def test_catalog_query_fallback_reraises_when_list_all_also_fails(
    client_cls, path, recent_attr
):
    class QueryFallbackClient:
        def __init__(self):
            setattr(self, recent_attr, {})
            self.calls = []

        def get(self, request_path, **kwargs):
            self.calls.append((request_path, kwargs))
            assert request_path == path
            raise APIError("DataHub failed", status_code=500)

    client = QueryFallbackClient()
    with pytest.warns(RuntimeWarning), pytest.raises(APIError):
        client_cls(client).list(query="wanted")

    assert client.calls == [
        (path, {"params": {"query": "wanted"}}),
        (path, {}),
    ]
