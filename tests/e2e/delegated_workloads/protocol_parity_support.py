"""Typed SDK adapter and shared expectations for protocol parity vectors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from kamiwaza_sdk.delegated_workloads import (
    DelegatedGuardAuthority,
    DelegatedWorkloadAPI,
    DelegatedWorkloadError,
    DelegatedWorkloadTransport,
    EffectAuthorizationRequest,
    EffectConsumptionRequest,
    OneUseToken,
    WorkloadProof,
)
from kamiwaza_sdk.delegated_workloads.transport import checked_json_response

from .raw_http_conformance_client import (
    ErrorObservation,
    ExchangeObservation,
    RawHTTPNeutralClient,
    RawResponse,
    RecordingSession,
)

_BASE_URL = "https://core.example.test/api/v1/delegated-workloads"
_CAPABILITY = "header.parity-effect-capability.signature"
_ASSERTION = "neutral-parity-workload-assertion"
_CONSUMPTION = "one-use-consumption-token"
_NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)


class PythonSDKConformanceClient:
    """Execute the published exchanges through typed public SDK models."""

    def __init__(self, session: RecordingSession) -> None:
        self._session = session

    def execute(self, exchange: Mapping[str, Any]) -> ExchangeObservation:
        request = _mapping(exchange, "request")
        transport = DelegatedWorkloadTransport(
            self._session,
            proof=WorkloadProof.proof_only(),
            clock=lambda: _NOW,
        )
        try:
            api = DelegatedWorkloadAPI(_BASE_URL, transport)
            if exchange.get("id") == "authorize-protected-effect":
                result = api.authorize_effect(_authorization(request), _authority())
            else:
                result = api.consume_effect(
                    _consumption(request), _authority(_CONSUMPTION)
                )
            return _model_observation(result)
        finally:
            transport.close()

    def error(self, mapping: Mapping[str, Any]) -> ErrorObservation:
        code = _string(mapping, "code")
        response = self._session.request(
            "GET",
            f"{_BASE_URL}/conformance-errors/{code}",
        )
        try:
            checked_json_response(response)
        except DelegatedWorkloadError as exc:
            return ErrorObservation(
                response.status_code,
                exc.code.value,
                exc.retry_classification.value,
            )
        raise AssertionError("SDK accepted a published error response")


def _authorization(request: Mapping[str, Any]) -> EffectAuthorizationRequest:
    body = _mapping(request, "body")
    return EffectAuthorizationRequest(
        effect_id=UUID(_string(body, "effect_id")),
        request_digest=_string(body, "request_digest"),
        method=_string(body, "method"),
        target_uri=_string(body, "target_uri"),
    )


def _consumption(request: Mapping[str, Any]) -> EffectConsumptionRequest:
    body = _mapping(request, "body")
    effect_id = _string(request, "path").split("/effects/", 1)[1].split("/", 1)[0]
    fence = body.get("fencing_token")
    if type(fence) is not int:
        raise AssertionError("published fencing token is invalid")
    return EffectConsumptionRequest(
        effect_id=UUID(effect_id),
        request_digest=_string(body, "request_digest"),
        fencing_token=fence,
    )


def _authority(consumption: str | None = None) -> DelegatedGuardAuthority:
    authority = DelegatedGuardAuthority(
        capability=_CAPABILITY,
        workload_assertion=_ASSERTION,
    )
    return authority.with_consumption(OneUseToken(consumption)) if consumption else authority


def _model_observation(result: object) -> ExchangeObservation:
    payload = result.model_dump(mode="json")  # type: ignore[attr-defined]
    context = payload.get("requester_context")
    return ExchangeObservation(
        200,
        str(payload["effect_id"]),
        str(payload.get("decision", payload.get("status"))),
        context,
        payload.get("consumption_token") is not None,
    )


def expected_exchange(exchange: Mapping[str, Any]) -> ExchangeObservation:
    response = _mapping(exchange, "response")
    body = _mapping(response, "body")
    context = body.get("requester_context")
    return ExchangeObservation(
        _integer(response, "status"),
        _string(body, "effect_id"),
        str(body.get("decision", body.get("status"))),
        context,
        body.get("consumption_token") is not None,
    )


def expected_error(mapping: Mapping[str, Any]) -> ErrorObservation:
    return ErrorObservation(
        _integer(mapping, "status"),
        _string(mapping, "code"),
        _string(mapping, "retry"),
    )


def error_response(mapping: Mapping[str, Any]) -> RawResponse:
    code = _string(mapping, "code")
    headers = {"DPoP-Nonce": "0123456789abcdef"} if code == "dpop_nonce_required" else {}
    return RawResponse(
        _integer(mapping, "status"),
        {
            "error": {
                "code": code,
                "message": "safe parity failure",
                "retry_classification": _string(mapping, "retry"),
                "correlation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "safe_details": {"source": "parity-vector"},
            }
        },
        headers,
    )


def response_for_exchange(exchange: Mapping[str, Any]) -> RawResponse:
    response = _mapping(exchange, "response")
    return RawResponse(
        _integer(response, "status"),
        _mapping(response, "body"),
    )


def _sdk_factory(session: RecordingSession) -> PythonSDKConformanceClient:
    return PythonSDKConformanceClient(session)


def _raw_factory(session: RecordingSession) -> RawHTTPNeutralClient:
    return RawHTTPNeutralClient(session, _BASE_URL)


def _mapping(value: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    candidate = value.get(name)
    if not isinstance(candidate, Mapping):
        raise AssertionError(f"published {name} is invalid")
    return candidate


def _string(value: Mapping[str, Any], name: str) -> str:
    candidate = value.get(name)
    if not isinstance(candidate, str):
        raise AssertionError(f"published {name} is invalid")
    return candidate


def _integer(value: Mapping[str, Any], name: str) -> int:
    candidate = value.get(name)
    if type(candidate) is not int:
        raise AssertionError(f"published {name} is invalid")
    return candidate


ClientFactory = Callable[[RecordingSession], object]
CLIENT_FACTORIES: tuple[ClientFactory, ...] = (_sdk_factory, _raw_factory)

__all__ = (
    "CLIENT_FACTORIES",
    "RecordingSession",
    "error_response",
    "expected_error",
    "expected_exchange",
    "response_for_exchange",
)
