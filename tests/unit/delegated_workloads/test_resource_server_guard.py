"""Fail-closed protected-resource guard and handler invocation contracts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from kamiwaza_sdk.delegated_workloads.resource_server import (
    CoreResourceGuardHTTPClient,
    ProtectedResourceRequest,
    ResourceGuardRejected,
    SealedDelegatedContext,
)
from kamiwaza_sdk.delegated_workloads.integrations.asgi import DelegatedResourceASGI
from kamiwaza_sdk.delegated_workloads.proof import OneUseToken
from tests.unit.delegated_workloads.resource_guard_support import (
    ISSUER,
    NOW,
    RUN_TYPE,
    GuardCase,
    RequestOverrides,
    denied_authorization,
    guard_case,
)


RequestChange = Callable[[GuardCase], ProtectedResourceRequest]


def test_guard_consumes_before_handler_and_seals_dual_principal_context() -> None:
    case = guard_case()
    invoked: list[SealedDelegatedContext] = []

    def handler(
        _request: ProtectedResourceRequest,
        context: SealedDelegatedContext,
    ) -> str:
        invoked.append(context)
        return "protected-result"

    result = case.guard.guard(case.registration, handler)(case.request())

    assert result == "protected-result"
    assert len(case.decisions.authorize_calls) == 1
    assert len(case.decisions.consume_calls) == 1
    assert invoked[0].subject_id == case.context.subject_id
    assert invoked[0].actor_id.startswith("urn:kamiwaza:workload:")
    assert invoked[0].subject_id.hex not in invoked[0].actor_id


@pytest.mark.parametrize(
    "change",
    (
        lambda case: case.request(
            RequestOverrides(
                payload={"token_type": RUN_TYPE, "token_class": "run_executor"},
                capability_header={"typ": RUN_TYPE},
            )
        ),
        lambda case: case.request(RequestOverrides(payload={"iss": ISSUER + ":other"})),
        lambda case: case.request(
            RequestOverrides(capability_header={"kid": "unknown-key"})
        ),
        lambda case: case.request(
            RequestOverrides(payload={"aud": "https://other.example.test"})
        ),
        lambda case: case.request(
            RequestOverrides(
                payload={"exp": int((NOW - timedelta(seconds=1)).timestamp())}
            )
        ),
        lambda case: case.request(
            RequestOverrides(signing_key=ec.generate_private_key(ec.SECP256R1()))
        ),
    ),
    ids=("token-class", "issuer", "key-id", "audience", "expiry", "signature"),
)
def test_invalid_capability_never_invokes_handler(change: RequestChange) -> None:
    case = guard_case()
    _assert_rejected_without_handler(case, change(case))
    assert not case.decisions.authorize_calls


@pytest.mark.parametrize(
    "change",
    (
        lambda case: case.request(
            RequestOverrides(proof_key=ec.generate_private_key(ec.SECP256R1()))
        ),
        lambda case: case.request(RequestOverrides(proof_payload={"htm": "GET"})),
        lambda case: case.request(
            RequestOverrides(proof_payload={"htu": "https://other.test/"})
        ),
        lambda case: case.request(
            RequestOverrides(proof_payload={"body_sha256": "sha256:" + "0" * 64})
        ),
        lambda case: case.request(
            RequestOverrides(proof_payload={"ath": "wrong-token-hash"})
        ),
        lambda case: case.request(
            RequestOverrides(
                proof_payload={"iat": int((NOW - timedelta(minutes=2)).timestamp())}
            )
        ),
    ),
    ids=("proof-key", "method", "target", "body-digest", "token-binding", "age"),
)
def test_invalid_proof_or_request_digest_never_invokes_handler(
    change: RequestChange,
) -> None:
    case = guard_case()
    _assert_rejected_without_handler(case, change(case))
    assert not case.decisions.authorize_calls


def test_descriptor_revision_mismatch_never_reaches_current_decision() -> None:
    case = guard_case()
    resource = case.context.resource.model_copy(update={"descriptor_version": "v2"})
    request = case.request(
        RequestOverrides(payload={"resources": [resource.model_dump(mode="json")]})
    )

    _assert_rejected_without_handler(case, request)

    assert not case.decisions.authorize_calls


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("resource_registration_revision_id", None),
        ("run_claim_id", None),
        ("fencing_token", 99),
        ("client_id", None),
    ),
)
def test_changed_context_revision_claim_fence_or_actor_never_consumes(
    field: str,
    value: object,
) -> None:
    case = guard_case()
    replacement = value or case.context.correlation_id
    try:
        context = case.context.model_copy(update={field: replacement})
        case.decisions.authorization = case.decisions.authorization.model_copy(
            update={"requester_context": context}
        )
    except ValidationError:
        pytest.fail("test context update must remain structurally valid")

    _assert_rejected_without_handler(case, case.request())

    assert len(case.decisions.authorize_calls) == 1
    assert not case.decisions.consume_calls


def test_current_deny_and_one_use_replay_never_reinvoke_handler() -> None:
    case = guard_case()
    handler_calls: list[object] = []
    guarded = case.guard.guard(
        case.registration,
        lambda _request, context: handler_calls.append(context),
    )
    case.decisions.authorization = denied_authorization(case)

    with pytest.raises(ResourceGuardRejected):
        guarded(case.request())
    assert not handler_calls
    assert not case.decisions.consume_calls

    case = guard_case()
    handler_calls = []
    guarded = case.guard.guard(
        case.registration,
        lambda _request, context: handler_calls.append(context),
    )
    guarded(case.request())
    case.decisions.consume_error = RuntimeError("raw consumption failure")
    with pytest.raises(ResourceGuardRejected, match="protected resource"):
        guarded(case.request())
    assert len(handler_calls) == 1


def test_context_cannot_be_forged_or_installed_from_inbound_headers() -> None:
    case = guard_case()
    with pytest.raises(TypeError):
        SealedDelegatedContext(case.context)

    request = case.request(
        RequestOverrides(
            extra_headers={
                "X-Kamiwaza-Delegated-Subject-Id": str(case.context.subject_id)
            }
        )
    )
    _assert_rejected_without_handler(case, request)
    assert not case.decisions.authorize_calls


def test_http_adapter_forwards_original_authority_and_consumes_separately() -> None:
    case = guard_case()
    case.guard.guard(case.registration, lambda _request, _context: None)(case.request())
    check = case.decisions.authorize_calls[0]
    authorization_payload = case.decisions.authorization.model_dump(
        mode="json",
        exclude={"consumption_token"},
    )
    authorization_payload["consumption_token"] = "consume-once"
    session = _HTTPSession(
        authorization_payload,
        case.decisions.consumption.model_dump(mode="json"),
    )
    client = CoreResourceGuardHTTPClient(
        "https://core.example.test/api/v1/delegated-workloads/",
        session,
    )

    assert client.authorize(check).effect_id == case.decisions.authorization.effect_id
    assert (
        client.consume(check, OneUseToken("consume-once")) == case.decisions.consumption
    )
    assert session.calls[0][1].endswith("/effect-authorizations")
    assert "X-Kamiwaza-Effect-Consumption" not in session.calls[0][2]
    assert session.calls[1][2]["X-Kamiwaza-Effect-Consumption"] == "consume-once"


@pytest.mark.asyncio
async def test_asgi_adapter_installs_sealed_context_after_consumption() -> None:
    case = guard_case()
    request = case.request()
    application_calls: list[SealedDelegatedContext] = []
    sent: list[dict[str, Any]] = []

    async def application(scope, receive, send) -> None:
        application_calls.append(scope["state"]["kamiwaza_delegated_context"])
        assert (await receive())["body"] == request.body
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = DelegatedResourceASGI(application, case.guard, case.registration)
    await middleware(
        _asgi_scope(request),
        _single_request(request.body),
        _capture(sent),
    )

    assert len(application_calls) == 1
    assert application_calls[0].subject_id == case.context.subject_id
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_asgi_adapter_denies_before_downstream_application() -> None:
    case = guard_case()
    request = case.request(
        RequestOverrides(extra_headers={"X-Kamiwaza-Delegated-Subject-Id": "forged"})
    )
    application_calls: list[object] = []
    sent: list[dict[str, Any]] = []

    async def application(_scope, _receive, _send) -> None:
        application_calls.append(object())

    middleware = DelegatedResourceASGI(application, case.guard, case.registration)
    await middleware(
        _asgi_scope(request),
        _single_request(request.body),
        _capture(sent),
    )

    assert not application_calls
    assert sent[0]["status"] == 403
    assert b"protected_resource_rejected" in sent[1]["body"]


def _assert_rejected_without_handler(
    case: GuardCase,
    request: ProtectedResourceRequest,
) -> None:
    invoked: list[object] = []
    guarded = case.guard.guard(
        case.registration,
        lambda _request, context: invoked.append(context),
    )

    with pytest.raises(ResourceGuardRejected, match="protected resource"):
        guarded(request)

    assert not invoked


class _HTTPResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _HTTPSession:
    def __init__(self, *payloads: object) -> None:
        self._payloads = list(payloads)
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> _HTTPResponse:
        headers = kwargs.get("headers")
        assert isinstance(headers, dict)
        self.calls.append((method, url, headers))
        return _HTTPResponse(self._payloads.pop(0))


def _asgi_scope(request: ProtectedResourceRequest) -> dict[str, Any]:
    headers = [(b"host", b"tickets.example.test")]
    headers.extend(
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in request.headers.items()
    )
    return {
        "type": "http",
        "method": request.method,
        "scheme": "https",
        "raw_path": b"/tickets/TKT-7",
        "query_string": b"",
        "headers": headers,
        "state": {},
    }


def _single_request(body: bytes):
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _capture(messages: list[dict[str, Any]]):
    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    return send
