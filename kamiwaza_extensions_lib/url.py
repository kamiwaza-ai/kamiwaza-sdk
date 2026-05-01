"""URL-resolution helpers shared by the runtime-lib and the app template.

Two distinct base-URL concepts; pick the right one for the call site:

* :func:`public_base_url` — **browser-facing** runtime URLs (model
  endpoints displayed in the UI, deployment access paths returned to
  the frontend). ``/api`` is stripped.

* :func:`backend_runtime_base` — **container-routable** runtime URLs
  used by code that runs *inside* the backend container (AsyncOpenAI
  base URLs, server-to-platform runtime calls). ``/api`` is stripped.

Auth endpoints (``/api/auth/login``, ``/api/auth/logout``) live UNDER
``/api`` and so use the raw URLs directly via inline
``(public ?: api).rstrip("/")`` precedence in :mod:`session` — they
intentionally do NOT route through these helpers.

In production both URLs typically point at the same gateway, so the
priority order is a no-op there. Under ``kz-ext dev local --auth`` the
two intentionally diverge (``KAMIWAZA_PUBLIC_API_URL=http://localhost:8000``
for browser, ``KAMIWAZA_API_URL=http://host.docker.internal:8000/api``
for container).

This module is the single source of truth — any call site that imports
its own copy of the priority logic risks the round-5/7/8 regressions
where one URL leaked into the other audience's code path.
"""

from __future__ import annotations

from .config import AuthConfig


def _strip_api_suffix(url: str) -> str:
    """Strip a trailing ``/api`` (and any extra slashes). Empty → empty.

    Trailing-slash variants normalize identically — ``"…/api"`` and
    ``"…/api/"`` both produce the same output (round-9 review caught a
    drift between this helper and ``local_dev.public_api_url_from``
    on the trailing-slash case).
    """
    if not url:
        return ""
    return url.rstrip("/").removesuffix("/api").rstrip("/")


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
