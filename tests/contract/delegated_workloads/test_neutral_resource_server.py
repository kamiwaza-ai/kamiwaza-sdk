"""Contract checks for the separately deployable neutral resource example."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from examples.delegated_resource_server.adapters import (
    DocumentBrokerAdapter,
    DocumentCanonicalizer,
    DocumentEntitlementAdapter,
    DocumentQuotaAdapter,
    DocumentResultNormalizer,
)
from examples.delegated_resource_server.app import (
    DocumentStore,
    ResourceApplicationConfig,
    build_application,
)
from examples.delegated_resource_server.runtime import (
    BoundedJwksProvider,
    ResourceRuntimeConfig,
)
from kamiwaza_sdk.delegated_workloads import (
    DecisionReasonCode,
    EffectAuthorization,
    EffectConsumption,
    EffectLifecycleStatus,
    ResourceGuardRegistration,
    ResourceRef,
)
from tests.unit.delegated_workloads.resource_guard_support import (
    GuardCase,
    RequestOverrides,
    guard_case,
)

from .protocol_test_support import StubResponse, StubSession


pytestmark = pytest.mark.contract
_ROOT = Path(__file__).parents[3]
_EXAMPLE = _ROOT / "examples/delegated_resource_server"
_AUDIENCE = "https://documents.example.test"


def test_descriptor_declares_new_read_mutation_quota_and_broker_contract() -> None:
    descriptor = _json_file("resource-registration.json")
    actions = descriptor["actions"]

    assert descriptor["resource_type"] == "conformance.document"
    assert descriptor["audiences"] == [_AUDIENCE]
    assert actions["read"]["effect_class"] == "read"
    assert actions["read"]["approval_class"] == "none"
    assert actions["mutate"]["effect_class"] == "mutation"
    assert actions["mutate"]["approval_class"] == "exact_it_approval"
    assert actions["mutate"]["quota_dimensions"] == [
        "document_operations",
        "mutation_bytes",
    ]
    assert descriptor["broker_operation_types"] == ["document.export"]
    assert descriptor["guard_contract_version"] == "guard:v1"


def test_example_is_separately_deployable_and_consumer_neutral() -> None:
    dockerfile = (_EXAMPLE / "Dockerfile").read_text(encoding="utf-8")
    compose = (_EXAMPLE / "docker-compose.yml").read_text(encoding="utf-8")
    source = "".join(path.read_text(encoding="utf-8") for path in _EXAMPLE.glob("*.py"))

    assert "USER 65532:65532" in dockerfile
    assert "--factory" in dockerfile
    assert "healthcheck:" in compose
    assert "kamiwaza_extensions" not in source
    assert "tomo" not in source.casefold()
    assert "/registrations/resources" not in source
    assert "reconcile_resource" not in source


@pytest.mark.asyncio
async def test_closed_resource_adapters_are_deterministic_and_safe() -> None:
    canonicalizer = DocumentCanonicalizer()
    entitlement = DocumentEntitlementAdapter()
    quota = DocumentQuotaAdapter()
    broker = DocumentBrokerAdapter()
    normalizer = DocumentResultNormalizer()

    assert canonicalizer.canonicalize("doc-7") == "document:doc-7"
    assert canonicalizer.request_digest({"title": "Hello"}).startswith("sha256:")
    with pytest.raises(ValueError):
        canonicalizer.canonicalize("../DOC-7")
    with pytest.raises(ValueError):
        canonicalizer.canonicalize(7)
    with pytest.raises(ValueError):
        canonicalizer.request_digest({"invalid": object()})
    assert entitlement.authorize({"entitled": True})
    assert not entitlement.authorize({"entitled": False})
    assert quota.reserve({"action": "mutate", "body_bytes": 14}) == {
        "document_operations": 1,
        "mutation_bytes": 14,
    }
    with pytest.raises(ValueError):
        quota.reserve({"action": "unknown"})
    with pytest.raises(ValueError):
        quota.reserve({"action": "mutate", "body_bytes": True})
    assert await broker.execute(
        {"operation_id": "document.export", "resource_id": "document:doc-7"}
    ) == {"operation_id": "document.export", "status": "queued"}
    with pytest.raises(ValueError):
        await broker.execute({"operation_id": "unknown"})
    with pytest.raises(ValueError):
        await broker.execute({"operation_id": "document.export"})
    assert normalizer.normalize(
        {"id": "document:doc-7", "status": "ready", "token": "secret"}
    ) == {"id": "document:doc-7", "status": "ready"}
    with pytest.raises(ValueError):
        normalizer.normalize("unsafe")


def test_runtime_configuration_and_jwks_cache_are_bounded() -> None:
    revision = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
    config = ResourceRuntimeConfig.from_environment(
        {
            "KAMIWAZA_DELEGATED_CORE_URL": (
                "https://core.example.test/api/v1/delegated-workloads"
            ),
            "RESOURCE_AUDIENCE": _AUDIENCE,
            "RESOURCE_REGISTRATION_REVISION_ID": revision,
        }
    )
    session = StubSession([StubResponse(200, {"keys": []})])
    provider = BoundedJwksProvider(session, "https://core.example.test/jwks")
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)

    assert config.registration_revision_id.hex == revision.replace("-", "")
    assert provider(now) == {"keys": []}
    assert provider(now + timedelta(seconds=1)) == {"keys": []}
    assert len(session.calls) == 1
    with pytest.raises(ValueError):
        BoundedJwksProvider(session, "https://core.example.test/jwks", timedelta(0))


@pytest.mark.parametrize(
    "response",
    [
        StubResponse(503, {"keys": []}),
        StubResponse(200, []),
        StubResponse(200, {"unknown": []}),
    ],
)
def test_jwks_refresh_fails_closed(response: StubResponse) -> None:
    provider = BoundedJwksProvider(
        StubSession([response]),
        "https://core.example.test/jwks",
    )

    with pytest.raises(ValueError):
        provider(datetime(2026, 8, 9, 12, tzinfo=timezone.utc))


@pytest.mark.parametrize(
    "values",
    [
        {},
        {
            "KAMIWAZA_DELEGATED_CORE_URL": (
                "http://core.example.test/api/v1/delegated-workloads"
            ),
            "RESOURCE_AUDIENCE": _AUDIENCE,
            "RESOURCE_REGISTRATION_REVISION_ID": "not-a-uuid",
        },
    ],
)
def test_runtime_configuration_rejects_missing_or_insecure_values(
    values: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        ResourceRuntimeConfig.from_environment(values)


@pytest.mark.asyncio
async def test_guarded_mutation_and_read_use_sealed_exact_resource_context() -> None:
    store = DocumentStore()
    mutation = _document_case("mutate")
    mutation_app = build_application(mutation.guard, _config(mutation), store)
    mutation_request = mutation.request(
        RequestOverrides(
            method="PUT",
            target_uri=_AUDIENCE + "/v1/documents/doc-7",
            body=b'{"title":"Hello"}',
        )
    )

    mutation_response = await _call(mutation_app, mutation_request)

    assert mutation_response.status == 200
    assert mutation_response.body["id"] == "document:doc-7"
    assert len(mutation.decisions.authorize_calls) == 1
    assert len(mutation.decisions.consume_calls) == 1

    read = _document_case("read")
    read_app = build_application(read.guard, _config(read), store)
    read_request = read.request(
        RequestOverrides(
            method="GET",
            target_uri=_AUDIENCE + "/v1/documents/doc-7",
            body=b"",
        )
    )

    read_response = await _call(read_app, read_request)

    assert read_response.status == 200
    assert read_response.body["title"] == "Hello"
    assert read_response.body["subject_id"] == str(read.context.subject_id)


@pytest.mark.asyncio
async def test_health_and_unknown_routes_do_not_enter_the_guard() -> None:
    case = _document_case("read")
    app = build_application(case.guard, _config(case))

    health = await _call_scope(app, _ScopeRequest("GET", "/healthz"))
    missing = await _call_scope(app, _ScopeRequest("GET", "/missing"))
    method = await _call_scope(
        app,
        _ScopeRequest("POST", "/v1/documents/doc-7"),
    )

    assert health.status == 200
    assert missing.status == 404
    assert method.status == 405
    assert not case.decisions.authorize_calls


@pytest.mark.asyncio
async def test_route_context_mismatch_and_malformed_mutation_fail_closed() -> None:
    mismatch = _document_case("mutate")
    mismatch_app = build_application(mismatch.guard, _config(mismatch))
    mismatch_request = mismatch.request(
        RequestOverrides(
            method="PUT",
            target_uri=_AUDIENCE + "/v1/documents/doc-8",
            body=b'{"title":"Wrong target"}',
        )
    )
    malformed = _document_case("mutate")
    malformed_app = build_application(malformed.guard, _config(malformed))
    malformed_request = malformed.request(
        RequestOverrides(
            method="PUT",
            target_uri=_AUDIENCE + "/v1/documents/doc-7",
            body=b'{"unexpected":"field"}',
        )
    )

    mismatch_response = await _call(mismatch_app, mismatch_request)
    malformed_response = await _call(malformed_app, malformed_request)

    assert mismatch_response.status == 403
    assert malformed_response.status == 403


def _document_case(action: str) -> GuardCase:
    case = guard_case()
    registration = ResourceGuardRegistration(
        resource_type="conformance.document",
        descriptor_version="v1",
        revision_id=case.registration.revision_id,
        audience=_AUDIENCE,
        action=action,
        guard_contract_version="guard:v1",
    )
    context = case.context.model_copy(
        update={
            "resource_registration_revision_id": registration.revision_id,
            "action": action,
            "resource": ResourceRef(
                type=registration.resource_type,
                descriptor_version=registration.descriptor_version,
                id="document:doc-7",
            ),
            "audience": registration.audience,
        }
    )
    case.registration = registration
    case.context = context
    case.decisions.authorization = EffectAuthorization(
        effect_id=context.effect_id,
        decision="allow",
        reason_codes=(DecisionReasonCode.ALLOWED,),
        requester_context=context,
        consumption_token="consume-once",
        correlation_id=context.correlation_id,
    )
    case.decisions.consumption = EffectConsumption(
        effect_id=context.effect_id,
        status=EffectLifecycleStatus.EXECUTING,
        requester_context=context,
        correlation_id=context.correlation_id,
    )
    return case


def _config(case: GuardCase) -> ResourceApplicationConfig:
    return ResourceApplicationConfig(
        audience=case.registration.audience,
        registration_revision_id=case.registration.revision_id,
    )


class _Result:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.status = messages[0]["status"]
        self.body = json.loads(messages[1].get("body", b"{}"))


@dataclass(frozen=True, slots=True)
class _ScopeRequest:
    method: str
    target: str
    body: bytes = b""
    headers: tuple[tuple[bytes, bytes], ...] = ()


async def _call(app: Any, request: Any) -> _Result:
    target = request.target_uri.removeprefix(_AUDIENCE)
    headers = [
        (name.encode(), value.encode()) for name, value in request.headers.items()
    ]
    headers.append((b"host", b"documents.example.test"))
    return await _call_scope(
        app,
        _ScopeRequest(
            request.method,
            target,
            request.body,
            tuple(headers),
        ),
    )


async def _call_scope(app: Any, request: _ScopeRequest) -> _Result:
    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": request.body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": request.method,
            "scheme": "https",
            "path": request.target,
            "raw_path": request.target.encode(),
            "query_string": b"",
            "headers": list(request.headers) or [(b"host", b"documents.example.test")],
        },
        receive,
        send,
    )
    return _Result(sent)


def _json_file(name: str) -> dict[str, Any]:
    value = json.loads((_EXAMPLE / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value
