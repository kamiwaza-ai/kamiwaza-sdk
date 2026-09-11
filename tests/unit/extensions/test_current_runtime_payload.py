"""Exercise TLS and callback contracts at the SDK HTTP serialization boundary."""

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

pytestmark = [pytest.mark.unit, pytest.mark.extension_regression]


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
    ]
    return {
        "services": {
            name: {
                "image": f"registry.test/{name}:1.0.0",
                "ports": ["8000"],
                "environment": list(environment),
            }
            for name in ("frontend", "backend")
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


def _http_body(payload, operation):
    client = Mock()
    response = {"name": payload.name, "type": "app", "version": "1.0.0"}
    client.post.return_value = response
    client.patch.return_value = response
    service = ExtensionService(client)
    if operation == "create":
        service.create_extension(payload)
        client.post.assert_called_once()
        return client.post.call_args.kwargs["json"]
    patch = PatchExtension(
        **_build_patch_kwargs(_build_patch_service_specs(payload), payload)
    )
    service.patch_extension(payload.name, patch)
    client.patch.assert_called_once()
    return client.patch.call_args.kwargs["json"]


@pytest.mark.parametrize("operation", ["create", "patch"])
def test_tls_policy_replaces_conflicts_in_every_http_service(
    compose, tls_setting, operation
):
    original = deepcopy(compose)
    payload = _build_payload(compose)
    body = _http_body(payload, operation)

    assert payload.kamiwaza.tls_reject_unauthorized == ("1" if tls_setting else "0")
    assert "tls_reject_unauthorized" not in body["kamiwaza"]
    assert "tlsRejectUnauthorized" not in body["kamiwaza"]
    assert {service["name"] for service in body["services"]} == {"frontend", "backend"}
    for service in body["services"]:
        env = service["env"]
        assert [e["value"] for e in env if e["name"] == "KAMIWAZA_VERIFY_SSL"] == [
            str(tls_setting).lower()
        ]
        assert [
            e["value"] for e in env if e["name"] == "KAMIWAZA_TLS_REJECT_UNAUTHORIZED"
        ] == ["1" if tls_setting else "0"]
        assert {"name": "KEEP", "value": "unchanged"} in env
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
    import json

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
