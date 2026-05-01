"""URL-resolution helpers shared by the runtime-lib and the app template.

Two distinct base-URL concepts; pick the right one for the call site:

* :func:`public_base_url` — **browser-facing** values surfaced to the
  frontend / user (model endpoints displayed in the UI, OAuth redirect
  URLs, ``/auth/login-url`` responses, etc.).

* :func:`backend_runtime_base` — **container-routable** values used by
  code that runs *inside* the backend container (AsyncOpenAI base URLs,
  server-to-platform ``/auth/logout`` POSTs, etc.).

In production both URLs typically point at the same gateway, so the
priority order is a no-op there. Under ``kz-ext dev local --auth`` the
two intentionally diverge (``KAMIWAZA_PUBLIC_API_URL=localhost`` for
browser, ``KAMIWAZA_API_URL=host.docker.internal`` for container).

This module is the single source of truth — any call site that imports
its own copy of the priority logic risks the round-5/7/8 regressions
where one URL leaked into the other audience's code path.
"""

from __future__ import annotations

from .config import AuthConfig


def _strip_api_suffix(url: str) -> str:
    """Strip a trailing ``/api`` (and any extra slashes). Empty → empty."""
    return url.removesuffix("/api").rstrip("/") if url else ""


def public_base_url(config: AuthConfig) -> str:
    """Browser-facing base URL — for redirects and user-displayed values.

    Prefers ``config.public_api_url`` (the developer's browser-resolvable
    host) over ``config.api_url``. Under ``kz-ext dev local --auth`` the
    browser cannot resolve ``host.docker.internal``, so this MUST keep
    the original loopback host.
    """
    if config.public_api_url:
        return _strip_api_suffix(config.public_api_url)
    if config.api_url:
        return _strip_api_suffix(config.api_url)
    return ""


def backend_runtime_base(config: AuthConfig) -> str:
    """Container-routable base URL — for code that runs inside the backend.

    Prefers ``config.api_url`` (server-to-platform routable) over
    ``config.public_api_url``. Under ``kz-ext dev local --auth`` the
    backend container cannot reach its own ``localhost``, so this MUST
    use the rewritten ``host.docker.internal`` alias.

    PR #87 round-7 + round-8 caught two regressions where this priority
    wasn't honored: round-7 in ``_resolve_openai_base`` (lib), round-8 in
    the template's local ``_public_base_url`` and the
    ``/auth/logout`` server-side termination call.
    """
    if config.api_url:
        return _strip_api_suffix(config.api_url)
    if config.public_api_url:
        return _strip_api_suffix(config.public_api_url)
    return ""
