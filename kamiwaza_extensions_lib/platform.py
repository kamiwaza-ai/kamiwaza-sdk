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
from .errors import PlatformOutageError, PlatformRedirectError, UnexpectedContextError
from .url import CallbackTarget, _strip_api_suffix, callback_target, registered_api_url

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


def _validate_platform_path(path: str) -> None:
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


def _platform_url(path: str, config: AuthConfig) -> CallbackTarget:
    """Resolve a root-relative path through standard callback transport."""
    _validate_platform_path(path)
    standard_transport = config.platform_gateway_url.strip()
    raw_base = (
        registered_api_url(config)
        if standard_transport
        else config.api_url.strip() or registered_api_url(config)
    )
    if not raw_base:
        raise UnexpectedContextError(
            "KAMIWAZA_API_URL or KAMIWAZA_PUBLIC_API_URL is required "
            "for request-bound platform calls"
        )
    registered_url = f"{_strip_api_suffix(raw_base).rstrip('/')}{path}"
    try:
        return callback_target(registered_url, standard_transport)
    except ValueError as exc:
        raise UnexpectedContextError(
            "KAMIWAZA_API_URL, KAMIWAZA_PUBLIC_API_URL, or "
            "KAMIWAZA_PLATFORM_GATEWAY_URL is not valid callback configuration"
        ) from exc


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


async def platform_request(
    request: Request,
    method: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> httpx.Response:
    """Call a canonical platform route with the incoming user's auth envelope.

    Registered public URL retains Host, path, TLS authority, and authorization.
    ``KAMIWAZA_PLATFORM_GATEWAY_URL`` changes only the connection origin.
    Absolute and scheme-relative paths are rejected. Redirects raise
    :class:`PlatformRedirectError` instead of forwarding credentials.

    The response is returned without calling ``raise_for_status`` so an
    extension can preserve the platform's 4xx/5xx status and error contract.

    Raises:
        ValueError: If caller-controlled request input is invalid.
        MisboundAuthError: If the forwarded platform envelope is malformed or
            contains an ambiguous duplicate field.
        UnexpectedContextError: If required runtime configuration is invalid.
        PlatformRedirectError: If the canonical platform route redirects.
        PlatformOutageError: If the platform transport fails.
    """
    forbidden = _FORBIDDEN_REQUEST_KWARGS.intersection(kwargs)
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise ValueError(f"platform_request does not accept {names}")
    if not is_http_token(method):
        raise ValueError("platform_request requires a valid HTTP method token")

    config = AuthConfig.from_env()
    target = _platform_url(path, config)
    outbound_headers = _request_headers(request.headers, headers)
    outbound_headers["Host"] = target.host
    kwargs["extensions"] = target.extensions
    kwargs["follow_redirects"] = False

    try:
        async with httpx.AsyncClient(
            verify=config.httpx_verify(),
            timeout=timeout,
            trust_env=False,
        ) as client:
            response = await client.request(
                method.upper(),
                target.url,
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
