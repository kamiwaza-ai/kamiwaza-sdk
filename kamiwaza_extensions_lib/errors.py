"""UAC-9d runtime-lib exception hierarchy.

Each class carries a canonical ``class_name`` string matching the
entries in ``exception_names.json``.  The CLI's ``exit_code_for()``
uses the class_name to produce the process exit code; ``kz-ext doctor``
uses it to surface a fix hint.

Design reference: §4.2.7 RuntimeLibExceptionHierarchy.
"""

from __future__ import annotations


class KamiwazaRuntimeError(Exception):
    """Base class for runtime-lib exceptions surfaced to extension authors."""

    class_name: str = "kamiwaza_runtime_error"


class MisboundAuthError(KamiwazaRuntimeError):
    """Required envelope header missing or malformed (post-Traefik)."""

    class_name = "misbound_auth"


class UnexpectedContextError(KamiwazaRuntimeError):
    """Envelope missing or shape mismatch (e.g., local-dev envelope in prod)."""

    class_name = "unexpected_context"


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
