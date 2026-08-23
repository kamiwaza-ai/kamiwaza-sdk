"""Dependency-free raw HTTP client for published delegated conformance vectors."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.request import Request


@dataclass(frozen=True, slots=True)
class ExchangeObservation:
    status: int
    effect_id: str
    outcome: str
    requester_context: Mapping[str, object] | None
    has_consumption_token: bool


@dataclass(frozen=True, slots=True)
class ErrorObservation:
    status: int
    code: str
    retry: str


@dataclass(frozen=True, slots=True)
class RecordingCall:
    method: str
    url: str
    body: bytes
    headers: Mapping[str, str]


class RawResponse:
    def __init__(
        self,
        status: int,
        body: object,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status_code = status
        self._body = body
        self.headers = dict(headers or {})

    def json(self) -> object:
        return self._body


class RecordingSession:
    """Small neutral HTTP server double shared by both parity clients."""

    def __init__(self, response: RawResponse) -> None:
        self._response = response
        self.calls: list[RecordingCall] = []

    def request(self, method: str, url: str, **kwargs: object) -> RawResponse:
        body = kwargs.get("data", b"")
        headers = kwargs.get("headers", {})
        if not isinstance(body, bytes) or not isinstance(headers, Mapping):
            raise AssertionError("raw HTTP request is invalid")
        self.calls.append(
            RecordingCall(method, url, body, _string_headers(headers))
        )
        return self._response


class RawHTTPNeutralClient:
    """Execute the JSON fixture without importing or emulating the Python SDK."""

    def __init__(self, session: RecordingSession, base_url: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")

    def execute(self, exchange: Mapping[str, Any]) -> ExchangeObservation:
        request = _mapping(exchange, "request")
        body = _json_bytes(request.get("body"))
        method = _string(request, "method")
        raw = Request(
            self._base_url + _string(request, "path"),
            data=body,
            headers=_headers(request),
            method=method,
        )
        response = self._session.request(
            method,
            raw.full_url,
            data=raw.data or b"",
            headers=dict(raw.header_items()),
        )
        return _exchange_observation(response)

    def error(self, mapping: Mapping[str, Any]) -> ErrorObservation:
        code = _string(mapping, "code")
        response = self._session.request(
            "GET",
            f"{self._base_url}/conformance-errors/{code}",
        )
        body = _response_mapping(response)
        error = _mapping(body, "error")
        return ErrorObservation(
            response.status_code,
            _string(error, "code"),
            _string(error, "retry_classification"),
        )


def _exchange_observation(response: RawResponse) -> ExchangeObservation:
    body = _response_mapping(response)
    outcome = body.get("decision", body.get("status"))
    if not isinstance(outcome, str):
        raise AssertionError("raw HTTP response outcome is invalid")
    context = body.get("requester_context")
    if context is not None and not isinstance(context, Mapping):
        raise AssertionError("raw HTTP requester context is invalid")
    return ExchangeObservation(
        response.status_code,
        _string(body, "effect_id"),
        outcome,
        context,
        body.get("consumption_token") is not None,
    )


def _response_mapping(response: RawResponse) -> Mapping[str, Any]:
    body = response.json()
    if not isinstance(body, Mapping):
        raise AssertionError("raw HTTP response is invalid")
    return body


def _mapping(value: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    candidate = value.get(name)
    if not isinstance(candidate, Mapping):
        raise AssertionError(f"raw HTTP {name} is invalid")
    return candidate


def _string(value: Mapping[str, Any], name: str) -> str:
    candidate = value.get(name)
    if not isinstance(candidate, str):
        raise AssertionError(f"raw HTTP {name} is invalid")
    return candidate


def _headers(request: Mapping[str, Any]) -> dict[str, str]:
    candidate = request.get("headers")
    if not isinstance(candidate, Mapping):
        raise AssertionError("raw HTTP headers are invalid")
    return _string_headers(candidate)


def _string_headers(headers: Mapping[object, object]) -> dict[str, str]:
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in headers.items()):
        raise AssertionError("raw HTTP headers are invalid")
    return {str(key): str(value) for key, value in headers.items()}


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


__all__ = (
    "ErrorObservation",
    "ExchangeObservation",
    "RawHTTPNeutralClient",
    "RawResponse",
    "RecordingCall",
    "RecordingSession",
)
