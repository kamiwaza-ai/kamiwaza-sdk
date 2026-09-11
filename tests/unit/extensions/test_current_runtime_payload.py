"""Exercise TLS and callback contracts at the SDK HTTP serialization boundary."""

import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from kamiwaza_extensions.commands.dev import (
    _build_patch_kwargs,
    _build_patch_service_specs,
)
from kamiwaza_extensions.connections import ConnectionInfo
from kamiwaza_extensions.payload_builder import (
    ANNOTATION_SERVICE_REF_REWRITES,
    PayloadBuilder,
)
from kamiwaza_sdk.schemas.extensions import PatchExtension
from kamiwaza_sdk.services.extensions import ExtensionService

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clear_tls_override(monkeypatch):
    monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)


@pytest.fixture(params=[False, True], ids=["insecure", "strict"])
def tls_setting(request, monkeypatch):
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", str(request.param).lower())
    return request.param


@pytest.fixture
def compose():
    environment = [
        "KAMIWAZA_VERIFY_SSL=false",
        "KAMIWAZA_VERIFY_SSL=true",
        "KAMIWAZA_TLS_REJECT_UNAUTHORIZED=0",
        "KAMIWAZA_TLS_REJECT_UNAUTHORIZED=1",
        "KEEP=unchanged",
        "KAMIWAZA_CA_BUNDLE=/mounted/company-ca.pem",
    ]
    return {
        "services": {
            name: {
                "image": f"registry.test/{name}:1.0.0",
                "ports": [] if name == "worker" else ["8000"],
                "environment": list(environment),
            }
            for name in ("frontend", "backend", "worker")
        }
    }


def _build_payload(compose, name="extension"):
    connection = ConnectionInfo(
        name="production",
        url="https://api.example.com/api",
        active=True,
        created_at=0.0,
    )
    return PayloadBuilder().build({"version": "1.0.0"}, compose, connection, name)


def _http_body(payload, operation, service_filter=None):
    client = Mock()
    response = {"name": payload.name, "type": "app", "version": "1.0.0"}
    client.post.return_value = response
    client.patch.return_value = response
    service = ExtensionService(client)
    if operation == "create":
        service.create_extension(payload)
        client.post.assert_called_once()
        return client.post.call_args.kwargs["json"]
    services = _build_patch_service_specs(payload, service_filter=service_filter)
    patch = PatchExtension(**_build_patch_kwargs(services, payload))
    service.patch_extension(payload.name, patch)
    client.patch.assert_called_once()
    return client.patch.call_args.kwargs["json"]


@pytest.mark.parametrize("operation", ["create", "patch"])
def test_tls_policy_replaces_conflicts_in_every_serialized_service(
    compose, tls_setting, operation
):
    original = deepcopy(compose)
    payload = _build_payload(compose)
    body = _http_body(payload, operation)

    assert payload.kamiwaza.tls_reject_unauthorized == ("1" if tls_setting else "0")
    assert "tls_reject_unauthorized" not in body["kamiwaza"]
    assert "tlsRejectUnauthorized" not in body["kamiwaza"]
    assert {service["name"] for service in body["services"]} == {
        "frontend",
        "backend",
        "worker",
    }
    for service in body["services"]:
        env = service["env"]
        assert [e["value"] for e in env if e["name"] == "KAMIWAZA_VERIFY_SSL"] == [
            str(tls_setting).lower()
        ]
        assert [
            e["value"] for e in env if e["name"] == "KAMIWAZA_TLS_REJECT_UNAUTHORIZED"
        ] == ["1" if tls_setting else "0"]
        assert {"name": "KEEP", "value": "unchanged"} in env
        assert {"name": "KAMIWAZA_CA_BUNDLE", "value": "/mounted/company-ca.pem"} in env
    assert compose == original


@pytest.mark.parametrize("operation", ["create", "patch"])
@pytest.mark.parametrize(
    "environment",
    [
        {"CALLBACK_URL": "http://backend:8000/callback"},
        ["CALLBACK_URL=http://backend:8000/callback"],
        [{"name": "CALLBACK_URL", "value": "http://backend:8000/callback"}],
        [{"CALLBACK_URL": "http://backend:8000/callback"}],
    ],
    ids=["mapping", "string-list", "name-value-list", "mapping-fragment-list"],
)
def test_self_callback_reaches_http_body_without_mutating_source(
    compose, environment, operation
):
    compose["services"]["backend"]["environment"] = environment
    original = deepcopy(compose)
    for name in ("first-deployment", "second-deployment"):
        body = _http_body(_build_payload(compose, name), operation)
        backend = next(s for s in body["services"] if s["name"] == "backend")
        expected = f"http://{name}-backend:8000/callback"
        assert {"name": "CALLBACK_URL", "value": expected} in backend["env"]
        rewrites = json.loads(body["annotations"][ANNOTATION_SERVICE_REF_REWRITES])
        assert rewrites["backend"]["CALLBACK_URL"] == {
            "from": "http://backend:8000/callback",
            "to": expected,
        }
        assert compose == original


def test_filtered_patch_refreshes_only_selected_service(compose, monkeypatch):
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
    initial = _http_body(_build_payload(compose), "create")
    assert all(
        {"name": "KAMIWAZA_VERIFY_SSL", "value": "false"} in service["env"]
        for service in initial["services"]
    )

    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "true")
    body = _http_body(_build_payload(compose), "patch", service_filter="backend")

    # Siblings receive no PATCH entry: a full redeploy is needed to refresh them.
    assert [service["name"] for service in body["services"]] == ["backend"]
    env = body["services"][0]["env"]
    assert {"name": "KAMIWAZA_VERIFY_SSL", "value": "true"} in env
    assert {"name": "KAMIWAZA_TLS_REJECT_UNAUTHORIZED", "value": "1"} in env
    assert "tls_reject_unauthorized" not in body["kamiwaza"]


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_loopback_callbacks_stay_in_pod(compose, host):
    url = f"http://{host}:8000/callback"
    endpoint = f"{host}:8000"
    compose["services"]["backend"]["environment"] = {
        "CALLBACK_URL": url,
        "CALLBACK_ENDPOINT": endpoint,
    }

    body = _http_body(_build_payload(compose), "create")

    backend = next(s for s in body["services"] if s["name"] == "backend")
    assert {"name": "CALLBACK_URL", "value": url} in backend["env"]
    assert {"name": "CALLBACK_ENDPOINT", "value": endpoint} in backend["env"]
    assert ANNOTATION_SERVICE_REF_REWRITES not in body["annotations"]
