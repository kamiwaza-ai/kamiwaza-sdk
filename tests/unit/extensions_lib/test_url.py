"""Direct tests for the public ``kamiwaza_extensions_lib.url`` helpers.

The helpers were promoted from a private ``_url`` module to a public
``url`` module in PR #87 round-9 (the template / chatbot example /
session router all call them, and importing a private path made the
public-API surface fragile). These tests cover:

* ``_strip_api_suffix`` trailing-slash parity (round-9 Claude High —
  ``"…/api"`` and ``"…/api/"`` MUST normalize identically, otherwise
  ``url.public_base_url`` and ``local_dev.public_api_url_from`` drift).
* The browser-vs-container priority semantics for both helpers.
* The ``api_url`` fallback used when ``public_api_url`` is unset (and
  the symmetric reverse case for ``backend_runtime_base``).
* Empty-string handling — both helpers return ``""`` (not ``None``) so
  callers can ``rstrip("/")`` without a guard.
"""

from __future__ import annotations

import pytest

from kamiwaza_extensions_lib.config import AuthConfig
from kamiwaza_extensions_lib.url import (
    _strip_api_suffix,
    backend_runtime_base,
    public_base_url,
)


# ---------------------------------------------------------------------------
# _strip_api_suffix
# ---------------------------------------------------------------------------


class TestStripApiSuffix:
    @pytest.mark.parametrize(
        "url, expected",
        [
            ("", ""),
            ("https://example.com/api", "https://example.com"),
            ("https://example.com/api/", "https://example.com"),
            ("https://example.com/api///", "https://example.com"),
            ("https://example.com", "https://example.com"),
            ("https://example.com/", "https://example.com"),
            # ``/api`` suffix stripped only — sub-paths beyond ``/api`` are preserved.
            ("https://example.com/api/v1", "https://example.com/api/v1"),
            ("https://example.com/foo/api", "https://example.com/foo"),
            # Bare host without scheme (defensive — should still normalise).
            ("localhost:8000/api", "localhost:8000"),
        ],
    )
    def test_normalizes_known_inputs(self, url, expected):
        assert _strip_api_suffix(url) == expected

    def test_trailing_slash_parity(self):
        """Round-9 Claude H — ``…/api`` and ``…/api/`` MUST produce the
        same output. Round-10 removed the sibling
        ``local_dev.public_api_url_from`` so this is now the only
        ``/api``-stripping path; the parity contract is locked in
        here so no future helper drifts again."""
        assert _strip_api_suffix("https://example.com/api") == _strip_api_suffix(
            "https://example.com/api/"
        )


# ---------------------------------------------------------------------------
# public_base_url — browser-facing
# ---------------------------------------------------------------------------


class TestPublicBaseUrl:
    def test_prefers_public_over_api(self):
        """``public_api_url`` is the developer's browser-resolvable host
        (e.g. ``http://localhost:8000`` under ``kz-ext dev local --auth``).
        It MUST take priority over ``api_url`` (which under the same
        flow gets rewritten to ``host.docker.internal`` for container
        routing — unreachable from the browser)."""
        cfg = AuthConfig(
            api_url="http://host.docker.internal:8000/api",
            public_api_url="http://localhost:8000",
        )
        assert public_base_url(cfg) == "http://localhost:8000"

    def test_falls_back_to_api_url(self):
        """Under production / ``USE_AUTH=false`` / non-bridge dev, only
        ``api_url`` is set — the helper must still produce a usable
        base URL by stripping the ``/api`` suffix."""
        cfg = AuthConfig(api_url="https://gateway.example.com/api")
        assert public_base_url(cfg) == "https://gateway.example.com"

    def test_returns_empty_when_unset(self):
        """Empty ``""`` (not ``None``) so callers can ``rstrip("/")``
        without a guard."""
        cfg = AuthConfig()
        assert public_base_url(cfg) == ""


# ---------------------------------------------------------------------------
# backend_runtime_base — container-routable
# ---------------------------------------------------------------------------


class TestBackendRuntimeBase:
    def test_prefers_api_over_public(self):
        """The reverse priority — code running INSIDE the backend
        container cannot reach ``localhost`` (that's the developer's
        host, not the container). It must use the ``host.docker.internal``
        rewrite carried by ``api_url``."""
        cfg = AuthConfig(
            api_url="http://host.docker.internal:8000/api",
            public_api_url="http://localhost:8000",
        )
        assert backend_runtime_base(cfg) == "http://host.docker.internal:8000"

    def test_falls_back_to_public_api_url(self):
        """When ``api_url`` is unset (e.g. the developer only configured
        ``KAMIWAZA_PUBLIC_API_URL``), fall back to it. Production
        deployments typically don't hit this path because the helm chart
        sets both URLs."""
        cfg = AuthConfig(public_api_url="https://browser-host.example.com")
        assert backend_runtime_base(cfg) == "https://browser-host.example.com"

    def test_returns_empty_when_unset(self):
        cfg = AuthConfig()
        assert backend_runtime_base(cfg) == ""

    def test_strips_api_suffix(self):
        """``api_url`` typically ends with ``/api``; the helper must
        strip it so AsyncOpenAI ``base_url`` callers can append
        ``/runtime/models/<id>/v1`` without producing a doubled
        ``/api/api`` path."""
        cfg = AuthConfig(api_url="https://gateway.example.com/api/")
        assert backend_runtime_base(cfg) == "https://gateway.example.com"
