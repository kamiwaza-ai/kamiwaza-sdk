"""Tests for model-endpoint URL routing in kamiwaza_extensions_lib.models.

Covers the audience-split URL helpers (public vs container base), the
``_resolve_openai_base`` end-to-end resolution scenarios, and the
``_rehost_to_container`` browser-only/same-netloc re-host rule
(PR #87 rounds 7-12; ENG-8766). Split from ``test_models.py`` so each
file keeps a single concern: that one covers the model listing/client
surface, this one covers endpoint construction.
"""

import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.unit
class TestRuntimeBaseSplit:
    """PR #87 round-7 review (codex P1) — under `kz-ext dev local --auth`
    KAMIWAZA_API_URL (container-routable) and KAMIWAZA_PUBLIC_API_URL
    (browser-routable) intentionally diverge. The two consumers in
    models.py must use the right one:

    - list_available_models() returns endpoints displayed in the UI →
      _public_base_url (public_api_url first, browser-resolvable)
    - _resolve_openai_base() builds AsyncOpenAI used by the backend →
      _backend_runtime_base (api_url first, container-routable)

    Without this split, get_model_client() would build URLs the backend
    container can't reach (its own localhost) and every --auth model
    call 401s on a misroute.
    """

    def test_public_base_prefers_public_api_url(self):
        # Browser-facing display path — pick the URL the browser sees.
        from kamiwaza_extensions_lib.config import AuthConfig
        from kamiwaza_extensions_lib.models import _public_base_url

        config = AuthConfig(
            api_url="http://host.docker.internal:8000/api",
            public_api_url="http://localhost:8000",
        )
        assert _public_base_url(config) == "http://localhost:8000"

    def test_public_base_falls_back_to_api_url(self):
        from kamiwaza_extensions_lib.config import AuthConfig
        from kamiwaza_extensions_lib.models import _public_base_url

        config = AuthConfig(
            api_url="https://kamiwaza.test/api",
            public_api_url="",
        )
        assert _public_base_url(config) == "https://kamiwaza.test"

    def test_backend_runtime_base_prefers_api_url(self):
        # Container-side path — pick the URL the backend container can
        # reach. Round-7 regression: previously _resolve_openai_base
        # built URLs from public_api_url which under --auth was kept as
        # localhost (browser-resolvable, container-unreachable).
        from kamiwaza_extensions_lib.config import AuthConfig
        from kamiwaza_extensions_lib.models import _backend_runtime_base

        config = AuthConfig(
            api_url="http://host.docker.internal:8000/api",
            public_api_url="http://localhost:8000",
        )
        assert _backend_runtime_base(config) == "http://host.docker.internal:8000"

    def test_backend_runtime_base_falls_back_to_public(self):
        # Edge case: api_url unset (shouldn't happen under --auth but
        # production deployments without an internal cluster URL fall
        # back to the public ingress).
        from kamiwaza_extensions_lib.config import AuthConfig
        from kamiwaza_extensions_lib.models import _backend_runtime_base

        config = AuthConfig(
            api_url="",
            public_api_url="https://kamiwaza.test/api",
        )
        assert _backend_runtime_base(config) == "https://kamiwaza.test"

    def test_production_parity_both_urls_equal(self):
        # In production deployed extensions both URLs typically point at
        # the same gateway. The split must be a no-op in that case.
        from kamiwaza_extensions_lib.config import AuthConfig
        from kamiwaza_extensions_lib.models import (
            _backend_runtime_base,
            _public_base_url,
        )

        config = AuthConfig(
            api_url="https://kamiwaza.example.com/api",
            public_api_url="https://kamiwaza.example.com/api",
        )
        assert _public_base_url(config) == _backend_runtime_base(config)

    @pytest.mark.asyncio
    async def test_resolve_openai_base_uses_container_url_under_auth_split(
        self, monkeypatch
    ):
        # End-to-end: simulate the round-5 split env (container + browser
        # URLs differ), build an OpenAI client, assert the resolved base
        # uses the container-routable host.

        monkeypatch.setenv(
            "KAMIWAZA_API_URL", "http://host.docker.internal:8000/api"
        )
        monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "http://localhost:8000")
        monkeypatch.setenv("KZ_EXT_DEV_LOCAL_AUTH", "1")

        from kamiwaza_extensions_lib.config import AuthConfig
        from kamiwaza_extensions_lib.models import _resolve_openai_base

        config = AuthConfig.from_env()

        # Stub out the platform call so we don't actually hit the network.
        with patch(
            "kamiwaza_extensions_lib.models.KamiwazaExtClient.from_env"
        ) as mock_from_env:
            mock_client = AsyncMock()
            mock_client.get_models = AsyncMock(
                return_value=[
                    {
                        "deployment_id": "dep-1",
                        "phase": "Running",
                        "type": "chat",
                        "access_path": "/runtime/models/dep-1/v1",
                    }
                ]
            )
            mock_from_env.return_value = mock_client

            base = await _resolve_openai_base(config, {})

        assert "host.docker.internal" in base, (
            f"_resolve_openai_base returned {base!r} — should be the "
            "container-routable host (host.docker.internal), NOT the "
            "browser-facing localhost (which the backend container "
            "cannot reach)."
        )
        assert "localhost" not in base

    @pytest.mark.asyncio
    async def test_resolve_openai_base_rehosts_browser_endpoint_field(
        self, monkeypatch
    ):
        """PR #87 round-12 review (codex P2) — when the platform emits
        a fully-qualified ``endpoint`` field with a browser-facing host
        (``http://localhost:8000/...``) but ``_resolve_openai_base`` is
        building AsyncOpenAI for the backend container, the endpoint
        MUST be re-hosted onto the container-routable base. Without
        the rehost, ``get_model_client()`` configures AsyncOpenAI with
        the container's own ``localhost`` and every chat call fails.
        """
        monkeypatch.setenv(
            "KAMIWAZA_API_URL", "http://host.docker.internal:8000/api"
        )
        monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "http://localhost:8000")
        monkeypatch.setenv("KZ_EXT_DEV_LOCAL_AUTH", "1")

        from kamiwaza_extensions_lib.config import AuthConfig
        from kamiwaza_extensions_lib.models import _resolve_openai_base

        config = AuthConfig.from_env()

        with patch(
            "kamiwaza_extensions_lib.models.KamiwazaExtClient.from_env"
        ) as mock_from_env:
            mock_client = AsyncMock()
            mock_client.get_models = AsyncMock(
                return_value=[
                    {
                        "deployment_id": "dep-1",
                        "phase": "Running",
                        "type": "chat",
                        # NOTE: NO ``access_path`` — the endpoint field
                        # is the only path. Endpoint is browser-only.
                        "endpoint": (
                            "http://localhost:8000/api/runtime/models/dep-1/v1"
                        ),
                    }
                ]
            )
            mock_from_env.return_value = mock_client

            base = await _resolve_openai_base(config, {})

        assert "host.docker.internal" in base, (
            f"endpoint not re-hosted onto container_base: {base!r} — "
            "browser-only ``localhost`` would be unreachable from the "
            "backend container."
        )
        assert "localhost" not in base
        # /api stripping still happens.
        assert "/api/runtime/" not in base
        assert "/runtime/models/dep-1/v1" in base


    @pytest.mark.asyncio
    async def test_resolve_openai_base_keeps_gateway_endpoint_in_cluster(
        self, monkeypatch
    ):
        """ENG-8766 regression — in-cluster (operator-injected env) the
        platform emits gateway model endpoints. They are routable as-is
        (given resolvable origin DNS) and MUST NOT be re-hosted onto
        ``KAMIWAZA_API_URL`` — that's the Ray Serve proxy, which has no
        ``/runtime/models/*`` route, and the re-host made every
        in-cluster chat call 404.
        """
        monkeypatch.setenv(
            "KAMIWAZA_API_URL",
            "http://core-api.kamiwaza.svc.cluster.local:7777/api",
        )
        monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "https://kamiwaza.test/api")
        monkeypatch.delenv("KZ_EXT_DEV_LOCAL_AUTH", raising=False)

        from kamiwaza_extensions_lib.config import AuthConfig
        from kamiwaza_extensions_lib.models import _resolve_openai_base

        config = AuthConfig.from_env()

        with patch(
            "kamiwaza_extensions_lib.models.KamiwazaExtClient.from_env"
        ) as mock_from_env:
            mock_client = AsyncMock()
            mock_client.get_models = AsyncMock(
                return_value=[
                    {
                        "deployment_id": "dep-1",
                        "phase": "Running",
                        "type": "chat",
                        "endpoint": (
                            "https://kamiwaza.test/runtime/models/dep-1/v1"
                        ),
                    }
                ]
            )
            mock_from_env.return_value = mock_client

            base = await _resolve_openai_base(config, {})

        assert base == "https://kamiwaza.test/runtime/models/dep-1/v1", (
            f"gateway endpoint was re-hosted: {base!r} — the Ray Serve "
            "proxy at KAMIWAZA_API_URL cannot serve /runtime/models/*."
        )

    @pytest.mark.asyncio
    async def test_resolve_openai_base_builds_access_path_on_gateway_in_cluster(
        self, monkeypatch
    ):
        """ENG-8766 review Critical (codex, verified live) — the raw
        ``/serving/deployments`` payload carries ONLY a relative
        ``access_path`` (no ``endpoint``). Building it onto
        ``KAMIWAZA_API_URL`` targets the k8s Ray Serve proxy, which has
        no ``/runtime/models/*`` routes. Relative model routes must be
        built on the public/gateway base whenever its host is not
        browser-only.
        """
        monkeypatch.setenv(
            "KAMIWAZA_API_URL",
            "http://core-api.kamiwaza.svc.cluster.local:7777/api",
        )
        monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "https://kamiwaza.test/api")
        monkeypatch.delenv("KZ_EXT_DEV_LOCAL_AUTH", raising=False)

        from kamiwaza_extensions_lib.config import AuthConfig
        from kamiwaza_extensions_lib.models import _resolve_openai_base

        config = AuthConfig.from_env()

        with patch(
            "kamiwaza_extensions_lib.models.KamiwazaExtClient.from_env"
        ) as mock_from_env:
            mock_client = AsyncMock()
            mock_client.get_models = AsyncMock(
                return_value=[
                    {
                        "deployment_id": "dep-1",
                        "phase": "Running",
                        "type": "chat",
                        # Observed live payload shape: access_path only.
                        "access_path": "/runtime/models/dep-1",
                    }
                ]
            )
            mock_from_env.return_value = mock_client

            base = await _resolve_openai_base(config, {})

        assert base == "https://kamiwaza.test/runtime/models/dep-1/v1", (
            f"access_path built on the wrong base: {base!r}"
        )

    @pytest.mark.asyncio
    async def test_resolve_openai_base_loopback_range_public_uses_container_base(
        self, monkeypatch
    ):
        """ENG-8766 re-review High #2 — a ``127.0.0.0/8`` range variant
        (``127.0.0.2``) as the public URL is still browser-only:
        ``_model_route_base`` must fall back to the container-routable
        base rather than hand the backend its own loopback.
        """
        monkeypatch.setenv(
            "KAMIWAZA_API_URL", "http://host.docker.internal:8000/api"
        )
        monkeypatch.setenv("KAMIWAZA_PUBLIC_API_URL", "http://127.0.0.2:8000")
        monkeypatch.setenv("KZ_EXT_DEV_LOCAL_AUTH", "1")

        from kamiwaza_extensions_lib.config import AuthConfig
        from kamiwaza_extensions_lib.models import _resolve_openai_base

        config = AuthConfig.from_env()

        with patch(
            "kamiwaza_extensions_lib.models.KamiwazaExtClient.from_env"
        ) as mock_from_env:
            mock_client = AsyncMock()
            mock_client.get_models = AsyncMock(
                return_value=[
                    {
                        "deployment_id": "dep-1",
                        "phase": "Running",
                        "type": "chat",
                        "access_path": "/runtime/models/dep-1",
                    }
                ]
            )
            mock_from_env.return_value = mock_client

            base = await _resolve_openai_base(config, {})

        assert base == "http://host.docker.internal:8000/runtime/models/dep-1/v1", (
            f"loopback-range public URL leaked into the model base: {base!r}"
        )


@pytest.mark.unit
class TestRehostToContainer:
    """PR #87 round-12 review (Claude M2) — direct unit coverage for
    the ``_rehost_to_container`` helper. The end-to-end rehost path
    is exercised via ``_resolve_openai_base`` but the helper itself
    has edge cases that should fail loudly if regressed (path-prefix
    de-duplication came from round-9/round-11 template reviews and
    has bitten this PR twice).
    """

    def test_rehosts_browser_endpoint_onto_container_base(self):
        from kamiwaza_extensions_lib.models import _rehost_to_container

        result = _rehost_to_container(
            "http://localhost:8000/runtime/models/dep-1/v1",
            "http://host.docker.internal:8000",
        )
        assert result == "http://host.docker.internal:8000/runtime/models/dep-1/v1"

    def test_preserves_ingress_subpath_when_endpoint_lacks_it(self):
        # ENG-8766 note: the endpoint host must be browser-only for the
        # re-host to fire at all — a real (non-loopback) host is kept
        # verbatim now, so this prefix-merge case is exercised with
        # ``localhost``.
        from kamiwaza_extensions_lib.models import _rehost_to_container

        result = _rehost_to_container(
            "http://localhost:8000/runtime/models/dep-1/v1",
            "https://gateway.example.com/foo",
        )
        assert result == "https://gateway.example.com/foo/runtime/models/dep-1/v1"

    def test_rehosts_loopback_range_variant(self):
        """ENG-8766 re-review High #2 — dev-local supports the full
        ``127.0.0.0/8`` loopback range (e.g. ``127.0.0.2``), not just the
        ``127.0.0.1`` literal. A range variant must still be treated as
        browser-only and re-hosted.
        """
        from kamiwaza_extensions_lib.models import _rehost_to_container

        result = _rehost_to_container(
            "http://127.0.0.2:8000/runtime/models/dep-1/v1",
            "http://host.docker.internal:8000",
        )
        assert result == "http://host.docker.internal:8000/runtime/models/dep-1/v1"

    def test_keeps_gateway_endpoint_on_different_real_host(self):
        """ENG-8766 regression — on k8s the platform advertises model
        endpoints as ingress-gateway URLs; ``KAMIWAZA_API_URL`` points at
        the Ray Serve proxy which does NOT serve ``/runtime/models/*``
        (the rewrite lives in per-deployment gateway VirtualServices).
        Re-hosting the gateway URL onto the container base produced an
        unroutable URL and every in-cluster chat call 404'd. A
        fully-qualified endpoint on a different, non-loopback host must
        be kept verbatim.
        """
        from kamiwaza_extensions_lib.models import _rehost_to_container

        result = _rehost_to_container(
            "https://kamiwaza.test/runtime/models/dep-1/v1",
            "http://core-api.kamiwaza.svc.cluster.local:7777",
        )
        assert result == "https://kamiwaza.test/runtime/models/dep-1/v1"

    def test_merges_prefix_when_endpoint_already_on_container_netloc(self):
        # Same-netloc endpoints still get the sub-path prefix merge
        # (round-9/round-11 behavior) even though the host is not
        # browser-only — the netloc match is the signal that the
        # endpoint is already container-routable.
        from kamiwaza_extensions_lib.models import _rehost_to_container

        result = _rehost_to_container(
            "https://gateway.example.com/runtime/models/dep-1/v1",
            "https://gateway.example.com/foo",
        )
        assert result == "https://gateway.example.com/foo/runtime/models/dep-1/v1"

    def test_does_not_double_prepend_when_endpoint_already_has_prefix(
        self,
    ):
        # Round-9 / round-11 regression — the prepend MUST be skipped
        # when ``parsed.path`` already starts with the prefix.
        from kamiwaza_extensions_lib.models import _rehost_to_container

        result = _rehost_to_container(
            "https://gateway.example.com/foo/runtime/models/dep-1/v1",
            "https://gateway.example.com/foo",
        )
        assert "/foo/foo/" not in result
        assert result == "https://gateway.example.com/foo/runtime/models/dep-1/v1"

    def test_returns_unchanged_when_target_base_empty(self):
        from kamiwaza_extensions_lib.models import _rehost_to_container

        result = _rehost_to_container(
            "https://example.com/runtime/models/dep-1/v1", "",
        )
        assert result == "https://example.com/runtime/models/dep-1/v1"

    def test_returns_empty_for_empty_endpoint(self):
        from kamiwaza_extensions_lib.models import _rehost_to_container

        assert _rehost_to_container("", "https://gateway.example.com") == ""

    def test_skips_rehost_when_endpoint_lacks_scheme_or_netloc(self):
        from kamiwaza_extensions_lib.models import _rehost_to_container

        # Path-only endpoint — leave it alone, the caller is
        # responsible for treating it as a relative URL.
        assert (
            _rehost_to_container(
                "/runtime/models/dep-1/v1",
                "http://host.docker.internal:8000",
            )
            == "/runtime/models/dep-1/v1"
        )

    def test_skips_rehost_when_target_base_lacks_scheme_or_netloc(self):
        from kamiwaza_extensions_lib.models import _rehost_to_container

        # Defensive — a malformed ``container_base`` shouldn't corrupt
        # an otherwise-valid endpoint.
        assert (
            _rehost_to_container(
                "https://example.com/runtime/models/dep-1/v1",
                "not-a-url",
            )
            == "https://example.com/runtime/models/dep-1/v1"
        )
