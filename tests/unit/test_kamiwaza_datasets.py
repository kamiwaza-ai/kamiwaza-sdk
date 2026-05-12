"""T5.6 — kamiwaza.datasets module tests.

Customer-facing surface for catalog datasets + attribute-gate binding:

    kz.datasets.create(name, platform, **kwargs) -> DatasetRef
    kz.datasets.get(urn)                          -> DatasetRef
    kz.datasets.delete(urn)                       -> None
    kz.datasets.set_gate(urn, type, config={})    -> AttributeGateBinding
    kz.datasets.get_gate(urn)                     -> AttributeGateBinding
    kz.datasets.clear_gate(urn)                   -> None

Server-side correlates:
    POST   /api/catalog/datasets/
    GET    /api/catalog/datasets/by-urn?urn=...
    DELETE /api/catalog/datasets/by-urn?urn=...
    PUT    /api/catalog/datasets/{urn}/gate
    GET    /api/catalog/datasets/{urn}/gate
    DELETE /api/catalog/datasets/{urn}/gate
"""

from __future__ import annotations

import json
from typing import Any

import pytest


def test_kamiwaza_exposes_datasets_attribute() -> None:
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    assert client.datasets is not None


def test_datasets_is_lazy_loaded() -> None:
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    a = client.datasets
    b = client.datasets
    assert a is b


# ─── create / get / delete ────────────────────────────────────────────────


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_create_posts_minimal_body(httpx_mock: Any) -> None:
    """kz.datasets.create(name, platform) → POST /api/catalog/datasets/."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import DatasetRef

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/catalog/datasets/",
        status_code=200,
        json={
            "urn": "urn:li:dataset:(local,demo,PROD)",
            "name": "demo",
            "platform": "file",
            "environment": "PROD",
            "properties": {},
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    ds = client.datasets.create(name="demo", platform="file")

    assert isinstance(ds, DatasetRef)
    assert ds.urn == "urn:li:dataset:(local,demo,PROD)"
    assert ds.name == "demo"

    request = httpx_mock.get_requests(method="POST")[0]
    body = json.loads(request.content)
    assert body["name"] == "demo"
    assert body["platform"] == "file"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_create_forwards_properties_and_environment(httpx_mock: Any) -> None:
    """Optional kwargs (properties, environment) reach the server unchanged."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/catalog/datasets/",
        status_code=200,
        json={
            "urn": "urn:li:dataset:(local,demo,DEV)",
            "name": "demo",
            "platform": "file",
            "environment": "DEV",
            "properties": {"path": "/data/demo"},
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.datasets.create(
        name="demo",
        platform="file",
        environment="DEV",
        properties={"path": "/data/demo"},
    )

    request = httpx_mock.get_requests(method="POST")[0]
    body = json.loads(request.content)
    assert body["environment"] == "DEV"
    assert body["properties"] == {"path": "/data/demo"}


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_get_passes_urn_as_query_param(httpx_mock: Any) -> None:
    """kz.datasets.get(urn) → GET /api/catalog/datasets/by-urn?urn=..."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import DatasetRef

    urn = "urn:li:dataset:(local,demo,PROD)"
    httpx_mock.add_response(
        method="GET",
        url=f"https://kamiwaza.test/api/catalog/datasets/by-urn?urn={urn}",
        status_code=200,
        json={
            "urn": urn,
            "name": "demo",
            "platform": "file",
            "environment": "PROD",
            "properties": {},
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    ds = client.datasets.get(urn)
    assert isinstance(ds, DatasetRef)
    assert ds.urn == urn


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_delete_passes_urn_as_query_param(httpx_mock: Any) -> None:
    """kz.datasets.delete(urn) → DELETE /api/catalog/datasets/by-urn?urn=..."""
    from kamiwaza.client import Kamiwaza

    urn = "urn:li:dataset:(local,demo,PROD)"
    httpx_mock.add_response(
        method="DELETE",
        url=f"https://kamiwaza.test/api/catalog/datasets/by-urn?urn={urn}",
        status_code=200,
        json={"message": "deleted"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.datasets.delete(urn)


# ─── gate binding (M3-specific surface) ──────────────────────────────────


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_set_gate_puts_to_dataset_scoped_endpoint(httpx_mock: Any) -> None:
    """kz.datasets.set_gate puts to /api/catalog/datasets/{urn}/gate."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import AttributeGateBinding

    urn = "urn:li:dataset:(local,demo,PROD)"
    httpx_mock.add_response(
        method="PUT",
        url=f"https://kamiwaza.test/api/catalog/datasets/{urn}/gate",
        status_code=200,
        json={
            "dataset_urn": urn,
            "type": "my_gate.ClassificationGate",
            "config": {"classification_field": "classification"},
            "gate_name": "classification-gate",
            "kind": "attribute",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    binding = client.datasets.set_gate(
        urn,
        type="my_gate.ClassificationGate",
        config={"classification_field": "classification"},
    )

    assert isinstance(binding, AttributeGateBinding)
    assert binding.kind == "attribute"
    assert binding.dataset_urn == urn

    request = httpx_mock.get_requests(method="PUT")[0]
    body = json.loads(request.content)
    assert body == {
        "type": "my_gate.ClassificationGate",
        "config": {"classification_field": "classification"},
    }


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_set_gate_defaults_config_to_empty_dict(httpx_mock: Any) -> None:
    """Omitting config sends an empty dict — server's config_schema()
    default-accepts gates with no configurable surface."""
    from kamiwaza.client import Kamiwaza

    urn = "urn:li:dataset:(local,demo,PROD)"
    httpx_mock.add_response(
        method="PUT",
        url=f"https://kamiwaza.test/api/catalog/datasets/{urn}/gate",
        status_code=200,
        json={
            "dataset_urn": urn,
            "type": "x.Gate",
            "config": {},
            "gate_name": "x",
            "kind": "attribute",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.datasets.set_gate(urn, type="x.Gate")

    request = httpx_mock.get_requests(method="PUT")[0]
    body = json.loads(request.content)
    assert body == {"type": "x.Gate", "config": {}}


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_get_gate_returns_binding(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import AttributeGateBinding

    urn = "urn:li:dataset:(local,demo,PROD)"
    httpx_mock.add_response(
        method="GET",
        url=f"https://kamiwaza.test/api/catalog/datasets/{urn}/gate",
        status_code=200,
        json={
            "dataset_urn": urn,
            "type": "g.G",
            "config": {},
            "gate_name": "g",
            "kind": "attribute",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    binding = client.datasets.get_gate(urn)

    assert isinstance(binding, AttributeGateBinding)
    assert binding.gate_name == "g"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_get_gate_raises_on_404_not_configured(httpx_mock: Any) -> None:
    """404 not_configured surfaces as KamiwazaError per T5.10 mapping."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import KamiwazaError

    urn = "urn:li:dataset:(local,demo,PROD)"
    httpx_mock.add_response(
        method="GET",
        url=f"https://kamiwaza.test/api/catalog/datasets/{urn}/gate",
        status_code=404,
        json={"detail": {"reason": "not_configured", "dataset_urn": urn}},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    with pytest.raises(KamiwazaError):
        client.datasets.get_gate(urn)


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_clear_gate_sends_delete(httpx_mock: Any) -> None:
    from kamiwaza.client import Kamiwaza

    urn = "urn:li:dataset:(local,demo,PROD)"
    httpx_mock.add_response(
        method="DELETE",
        url=f"https://kamiwaza.test/api/catalog/datasets/{urn}/gate",
        status_code=200,
        json={"deleted": True, "previous_type": "g.G"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.datasets.clear_gate(urn)
