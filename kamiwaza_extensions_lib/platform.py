"""Safe request-bound calls from an extension backend to the platform."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import Request

from ._headers import has_http_control_character, header_bytes, is_http_token
from .auth import is_forwarded_auth_header, platform_auth_httpx_headers
from .config import AuthConfig
from .errors import (
    PlatformOutageError,
    PlatformRedirectError,
    PlatformResponseTooLargeError,
    UnexpectedContextError,
)
from .url import _strip_api_suffix

_DEFAULT_TIMEOUT_SECONDS = 30.0
_FORBIDDEN_REQUEST_KWARGS = frozenset(
    {"auth", "cookies", "extensions", "follow_redirects"}
)
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_FORBIDDEN_HEADER_PREFIXES = (
    "x-auth-",
    "x-envoy-",
    "x-forwarded-",
    "x-user-",
    "x-workroom-",
)
_FORBIDDEN_ROUTING_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "expect",
        "forwarded",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "x-http-method-override",
        "x-original-uri",
        "x-original-url",
        "x-real-ip",
        "x-rewrite-url",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-port",
        "x-forwarded-proto",
    }
)


def _platform_url(path: str, config: AuthConfig) -> str:
    """Resolve a root-relative path against the container-routable base."""
    try:
        parsed_path = urlsplit(path)
    except ValueError as exc:
        raise ValueError("platform path must be a valid root-relative URL") from exc
    decoded_path = parsed_path.path
    while True:
        next_decoded_path = unquote(decoded_path)
        if next_decoded_path == decoded_path:
            break
        decoded_path = next_decoded_path
    has_dot_segment = any(segment in {".", ".."} for segment in decoded_path.split("/"))
    has_api_prefix = decoded_path == "/api" or decoded_path.startswith("/api/")
    path_is_invalid = any(
        (
            not path.startswith("/"),
            path.startswith("//"),
            decoded_path.startswith("//"),
            bool(parsed_path.scheme),
            bool(parsed_path.netloc),
            bool(parsed_path.query),
            bool(parsed_path.fragment),
            "\\" in decoded_path,
            has_http_control_character(path),
            has_http_control_character(decoded_path),
            has_dot_segment,
            not has_api_prefix,
        )
    )
    if path_is_invalid:
        raise ValueError(
            "platform path must be a root-relative HTTP path under '/api' without "
            "a query, fragment, or dot segment (for example, "
            "'/api/catalog/datasets/'); pass query parameters with params="
        )

    raw_base = config.api_url.strip()
    if not raw_base:
        raise UnexpectedContextError(
            "KAMIWAZA_API_URL is required for request-bound platform calls"
        )
    try:
        configured_base = urlsplit(raw_base)
        configured_hostname = configured_base.hostname
        configured_port = configured_base.port
    except ValueError as exc:
        raise UnexpectedContextError(
            "KAMIWAZA_API_URL is not a valid container-routable platform URL"
        ) from exc
    configured_base_is_invalid = any(
        (
            configured_base.scheme not in {"http", "https"},
            not configured_base.netloc,
            not configured_hostname,
            configured_port is not None and not 1 <= configured_port <= 65535,
            configured_base.username is not None,
            configured_base.password is not None,
            bool(configured_base.query),
            bool(configured_base.fragment),
            has_http_control_character(raw_base),
        )
    )
    if configured_base_is_invalid:
        raise UnexpectedContextError(
            "KAMIWAZA_API_URL is not a valid container-routable platform URL"
        )

    base = _strip_api_suffix(raw_base)
    return f"{base.rstrip('/')}{path}"


def _application_header_items(
    supplied: Mapping[str, str] | None,
) -> list[tuple[bytes, bytes]]:
    items = []
    for key, value in (supplied or {}).items():
        normalized = key.lower()
        if any(
            (
                is_forwarded_auth_header(normalized),
                normalized in _FORBIDDEN_ROUTING_HEADERS,
                normalized.startswith(_FORBIDDEN_HEADER_PREFIXES),
            )
        ):
            raise ValueError(
                f"platform_request manages authentication and routing header {key!r}"
            )
        items.append(header_bytes(key, value))
    return items


def _forwarded_header_items(
    incoming: Mapping[str, str],
) -> list[tuple[bytes, bytes]]:
    return list(platform_auth_httpx_headers(incoming).raw)


def _request_headers(
    incoming: Mapping[str, str], supplied: Mapping[str, str] | None
) -> httpx.Headers:
    """Merge application headers without permitting auth-envelope overrides."""
    return httpx.Headers(
        (*_application_header_items(supplied), *_forwarded_header_items(incoming))
    )


async def _bounded_response(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: httpx.Headers,
    limit: int,
    kwargs: dict[str, Any],
) -> httpx.Response:
    """Read at most limit bytes, without decompressing an untrusted response."""
    headers["Accept-Encoding"] = "identity"
    async with client.stream(method, url, headers=headers, **kwargs) as response:
        if response.status_code in _REDIRECT_STATUS_CODES:
            raise PlatformRedirectError(
                response.status_code,
                response.request.url.path,
                response.headers.get("location"),
            )
        if response.headers.get("content-encoding", "identity").strip().lower() not in (
            "",
            "identity",
        ):
            raise UnexpectedContextError(
                "Bounded platform responses require identity encoding"
            )
        body = bytearray()
        async for chunk in response.aiter_raw():
            if len(chunk) > limit - len(body):
                raise PlatformResponseTooLargeError(
                    "Platform response exceeds configured byte limit"
                )
            body.extend(chunk)
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=bytes(body),
            request=response.request,
            extensions=response.extensions,
        )


async def platform_request(
    request: Request,
    method: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Call a canonical platform path with the incoming user's auth envelope.

    The destination is resolved from the container-routable
    ``KAMIWAZA_API_URL``. Absolute and scheme-relative URLs are rejected so
    forwarded credentials cannot leave that origin. Redirects are deliberately
    disabled: a redirect response raises :class:`PlatformRedirectError`,
    prompting the caller to correct the platform path rather than risk losing
    auth headers.

    Set max_response_bytes to bound the returned body, including error bodies.
    Bounded mode requests identity encoding, rejects compressed responses, and
    closes the stream immediately on overflow without following redirects.

    The response is returned without calling ``raise_for_status`` so an
    extension can preserve the platform's 4xx/5xx status and error contract.

    Raises:
        ValueError: If caller-controlled request input is invalid.
        MisboundAuthError: If the forwarded platform envelope is malformed or
            contains an ambiguous duplicate field.
        UnexpectedContextError: If required runtime configuration is invalid.
        PlatformRedirectError: If the canonical platform route redirects.
        PlatformOutageError: If the platform transport fails.
        PlatformResponseTooLargeError: If the bounded response exceeds its limit.
    """
    if max_response_bytes is not None and (
        isinstance(max_response_bytes, bool)
        or not isinstance(max_response_bytes, int)
        or max_response_bytes < 1
    ):
        raise ValueError("max_response_bytes must be a positive integer")
    forbidden = _FORBIDDEN_REQUEST_KWARGS.intersection(kwargs)
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise ValueError(f"platform_request does not accept {names}")
    if not is_http_token(method):
        raise ValueError("platform_request requires a valid HTTP method token")

    config = AuthConfig.from_env()
    url = _platform_url(path, config)
    outbound_headers = _request_headers(request.headers, headers)
    kwargs["follow_redirects"] = False

    try:
        async with httpx.AsyncClient(
            verify=config.httpx_verify(),
            timeout=timeout,
            trust_env=False,
        ) as client:
            if max_response_bytes is not None:
                return await _bounded_response(
                    client,
                    method.upper(),
                    url,
                    outbound_headers,
                    max_response_bytes,
                    kwargs,
                )
            response = await client.request(
                method.upper(),
                url,
                headers=outbound_headers,
                **kwargs,
            )
    except httpx.InvalidURL as exc:
        raise ValueError("platform_request received an invalid platform path") from exc
    except httpx.TransportError as exc:
        raise PlatformOutageError(
            f"Kamiwaza platform request failed for {urlsplit(path).path!r}"
        ) from exc

    if response.status_code in _REDIRECT_STATUS_CODES:
        raise PlatformRedirectError(
            response.status_code,
            urlsplit(path).path,
            response.headers.get("location"),
        )
    return response
