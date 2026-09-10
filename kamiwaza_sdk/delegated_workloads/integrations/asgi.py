"""Dependency-free ASGI adapter for the protected-resource guard."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from kamiwaza_sdk.delegated_workloads.resource_server import (
    ProtectedResourceGuard,
    ProtectedResourceRequest,
    ResourceGuardRegistration,
    ResourceGuardRejected,
    SealedDelegatedContext,
)


ASGIScope = MutableMapping[str, Any]
ASGIMessage = MutableMapping[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]


class ASGIApp(Protocol):
    async def __call__(
        self,
        scope: ASGIScope,
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ResourceASGISettings:
    max_body_bytes: int = 1_048_576
    context_state_key: str = "kamiwaza_delegated_context"

    def __post_init__(self) -> None:
        if self.max_body_bytes < 1 or not self.context_state_key:
            raise ValueError("protected resource ASGI settings are invalid")


class DelegatedResourceASGI:
    """Consume authority before dispatch and install only a sealed context."""

    def __init__(
        self,
        app: ASGIApp,
        guard: ProtectedResourceGuard,
        registration: ResourceGuardRegistration,
        settings: ResourceASGISettings = ResourceASGISettings(),
    ) -> None:
        self._app = app
        self._guard = guard
        self._registration = registration
        self._settings = settings

    async def __call__(
        self,
        scope: ASGIScope,
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        try:
            body = await _request_body(receive, self._settings.max_body_bytes)
            request = _protected_request(scope, body)
            guarded = self._guard.guard(
                self._registration,
                self._dispatch(scope, body, send),
            )
            await guarded(request)
        except ResourceGuardRejected:
            await _deny(send)

    def _dispatch(
        self,
        scope: ASGIScope,
        body: bytes,
        send: ASGISend,
    ) -> Callable[
        [ProtectedResourceRequest, SealedDelegatedContext],
        Awaitable[None],
    ]:
        async def dispatch(
            _request: ProtectedResourceRequest,
            context: SealedDelegatedContext,
        ) -> None:
            guarded_scope = dict(scope)
            state = dict(cast(Mapping[str, object], scope.get("state", {})))
            state[self._settings.context_state_key] = context
            guarded_scope["state"] = state
            await self._app(guarded_scope, _replay_body(body), send)

        return dispatch


async def _request_body(receive: ASGIReceive, maximum: int) -> bytes:
    body = bytearray()
    while True:
        message = await receive()
        body.extend(_body_chunk(message))
        if len(body) > maximum:
            raise ResourceGuardRejected()
        if not message.get("more_body", False):
            return bytes(body)


def _body_chunk(message: Mapping[str, object]) -> bytes:
    if message.get("type") != "http.request":
        raise ResourceGuardRejected()
    chunk = message.get("body", b"")
    if not isinstance(chunk, bytes):
        raise ResourceGuardRejected()
    return chunk


def _protected_request(
    scope: Mapping[str, object], body: bytes
) -> ProtectedResourceRequest:
    headers = _headers(scope.get("headers"))
    host = headers.get("host")
    method = scope.get("method")
    scheme = scope.get("scheme")
    path = scope.get("raw_path", scope.get("path", ""))
    query = scope.get("query_string", b"")
    if not isinstance(host, str) or not isinstance(method, str):
        raise ResourceGuardRejected()
    if not isinstance(scheme, str) or not isinstance(path, (bytes, str)):
        raise ResourceGuardRejected()
    path_text = path.decode("latin-1") if isinstance(path, bytes) else path
    query_text = query.decode("latin-1") if isinstance(query, bytes) else ""
    suffix = path_text + ("?" + query_text if query_text else "")
    return ProtectedResourceRequest(
        method=method,
        target_uri=f"{scheme}://{host}{suffix}",
        body=body,
        headers=headers,
    )


def _headers(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        raise ResourceGuardRejected()
    headers: dict[str, str] = {}
    for item in value:
        key, content = _header_item(item)
        if key in headers:
            raise ResourceGuardRejected()
        headers[key] = content
    return headers


def _header_item(value: object) -> tuple[str, str]:
    name, content = _header_pair(value)
    if not all((isinstance(name, bytes), isinstance(content, bytes))):
        raise ResourceGuardRejected()
    raw_name = cast(bytes, name)
    raw_content = cast(bytes, content)
    return raw_name.decode("latin-1").lower(), raw_content.decode("latin-1")


def _header_pair(value: object) -> tuple[object, object]:
    if not isinstance(value, tuple):
        raise ResourceGuardRejected()
    if len(value) != 2:
        raise ResourceGuardRejected()
    return value


def _replay_body(body: bytes) -> ASGIReceive:
    delivered = False

    async def receive() -> ASGIMessage:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


async def _deny(send: ASGISend) -> None:
    body = json.dumps(
        {"error": {"code": "protected_resource_rejected"}},
        separators=(",", ":"),
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


__all__ = (
    "ASGIApp",
    "DelegatedResourceASGI",
    "ResourceASGISettings",
)
