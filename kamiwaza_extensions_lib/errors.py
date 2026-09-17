"""UAC-9d runtime-lib exception hierarchy.

Each class carries a canonical ``class_name`` string matching the
entries in ``exception_names.json``.  The CLI's ``exit_code_for()``
uses the class_name to produce the process exit code; ``kz-ext doctor``
uses it to surface a fix hint.

Design reference: §4.2.7 RuntimeLibExceptionHierarchy.
"""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit


def _redirect_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _redirect_port(parsed: SplitResult) -> str:
    try:
        return f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return ""


def _redirect_origin(parsed: SplitResult) -> str:
    """Build a credential-free origin from a parsed redirect target."""
    host = _redirect_host(parsed.hostname or "")
    if not host:
        return ""
    scheme = f"{parsed.scheme}:" if parsed.scheme else ""
    return f"{scheme}//{host}{_redirect_port(parsed)}"


def _redirect_target(location: str | None) -> tuple[str, str]:
    if not location:
        return ("", "")
    try:
        parsed = urlsplit(location)
    except ValueError:
        return ("", "")
    return (parsed.path, _redirect_origin(parsed))


def _redirect_target_description(path: str, origin: str) -> str:
    target_parts = []
    if origin:
        target_parts.append(f"origin is {origin!r}")
    if path:
        target_parts.append(f"path is {path!r}")
    return f"; redirect target {' and '.join(target_parts)}" if target_parts else ""


class KamiwazaRuntimeError(Exception):
    """Base class for runtime-lib exceptions surfaced to extension authors."""

    class_name: str = "kamiwaza_runtime_error"


class MisboundAuthError(KamiwazaRuntimeError):
    """Required gateway-attested envelope header missing or malformed."""

    class_name = "misbound_auth"


class UnexpectedContextError(KamiwazaRuntimeError):
    """Envelope missing or shape mismatch (e.g., local-dev envelope in prod)."""

    class_name = "unexpected_context"


class PlatformRedirectError(UnexpectedContextError):
    """A platform call used a non-canonical URL that returned a redirect.

    Redirects are never followed for request-bound platform calls because HTTP
    clients may discard manually forwarded cookies or authorization headers.
    The caller must use the canonical platform path instead.
    """

    class_name = "platform_redirect"

    def __init__(self, status_code: int, path: str, location: str | None) -> None:
        self.status_code = status_code
        self.path = path
        location_path, location_origin = _redirect_target(location)
        self.location = location_path or None
        self.location_origin = location_origin or None
        destination = _redirect_target_description(location_path, location_origin)
        super().__init__(
            f"Kamiwaza platform returned HTTP {status_code} for {path!r}{destination}. "
            "Use the canonical platform path; authenticated redirects are disabled."
        )


class OutOfEnvelopeAccessError(KamiwazaRuntimeError):
    """Attempt to access resources outside envelope (cross-workroom etc.)."""

    class_name = "out_of_envelope_access"


class PlatformOutageError(KamiwazaRuntimeError):
    """Platform API 5xx or unreachable."""

    class_name = "platform_outage"


class StreamInterruptedError(KamiwazaRuntimeError):
    """Upstream streaming response failed after bytes were committed downstream.

    Surfaces from ``TokenRefreshMiddleware`` (ENG-3895) when the upstream
    connection drops or sends an SSE error frame after the response has
    already begun flowing to the extension client. By that point, retry is
    impossible — the HTTP status was committed at first-byte. Extension
    SDKs should map a connection close mid-stream to this class.
    """

    class_name = "stream_interrupted"
