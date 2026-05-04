"""Tests for PayloadBuilder."""

import pytest

from kamiwaza_extensions.connections import ConnectionInfo
from kamiwaza_extensions.payload_builder import (
    ANNOTATION_BUILD_HOST,
    ANNOTATION_DEPLOYED_AT,
    ANNOTATION_DEPLOYER,
    ANNOTATION_REVISION,
    ANNOTATION_SERVICE_REF_REWRITES,
    PayloadBuilder,
)


@pytest.fixture
def builder():
    return PayloadBuilder()


@pytest.fixture
def connection():
    return ConnectionInfo(
        name="test",
        url="https://cluster.kamiwaza.test/api",
        active=True,
        created_at=0.0,
    )


@pytest.fixture
def metadata():
    return {
        "name": "my-app",
        "version": "1.0.0",
        "source_type": "kamiwaza",
        "risk_tier": 1,
        "verified": False,
    }


@pytest.fixture
def transformed_compose():
    return {
        "services": {
            "frontend": {
                "image": "registry.test/my-app-frontend:1.0.0-dev",
                "ports": ["3000"],
                "environment": ["NEXT_PUBLIC_API_URL=http://backend:8000"],
                "deploy": {"resources": {"limits": {"cpus": "0.5", "memory": "512M"}}},
            },
            "backend": {
                "image": "registry.test/my-app-backend:1.0.0-dev",
                "ports": ["8000"],
                "environment": {
                    "OPENAI_BASE_URL": "http://host.docker.internal:8080",
                },
                "deploy": {"resources": {"limits": {"cpus": "1.0", "memory": "1G"}}},
            },
        },
    }


class TestBuild:
    def test_produces_valid_payload(
        self, builder, metadata, transformed_compose, connection
    ):
        payload = builder.build(
            metadata, transformed_compose, connection, "my-app-dev-abc123"
        )
        assert payload.name == "my-app-dev-abc123"
        assert payload.type == "app"
        assert payload.version == "1.0.0"
        assert len(payload.services) == 2

    def test_primary_service_assigned(
        self, builder, metadata, transformed_compose, connection
    ):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        primaries = [s for s in payload.services if s.primary]
        assert len(primaries) == 1
        assert primaries[0].name == "frontend"

    def test_ports_parsed(self, builder, metadata, transformed_compose, connection):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        fe = next(s for s in payload.services if s.name == "frontend")
        assert len(fe.ports) == 1
        assert fe.ports[0].container_port == 3000

    def test_kamiwaza_integration(
        self, builder, metadata, transformed_compose, connection
    ):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        assert payload.kamiwaza.api_url == "https://cluster.kamiwaza.test/api"
        assert payload.kamiwaza.use_auth == "true"

    def test_security_from_metadata(
        self, builder, metadata, transformed_compose, connection
    ):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        assert payload.security.risk_tier == 1


class TestAnnotations:
    """ENG-3887 / §4.2.9 — DeployedImageAnnotation on the CRD payload."""

    def test_annotations_present_when_deployer_and_revision_supplied(
        self,
        builder,
        metadata,
        transformed_compose,
        connection,
    ):
        payload = builder.build(
            metadata,
            transformed_compose,
            connection,
            "my-app-dev-abc",
            deployer="jonathan@kamiwaza.ai",
            revision="1.0.0-dev-a1b2c3d.1714000000",
        )
        # `annotations` rides on `extra="allow"` — read via model_extra.
        annotations = (payload.model_extra or {}).get("annotations")
        assert annotations is not None
        assert annotations[ANNOTATION_DEPLOYER] == "jonathan@kamiwaza.ai"
        assert annotations[ANNOTATION_REVISION] == "1.0.0-dev-a1b2c3d.1714000000"
        assert ANNOTATION_BUILD_HOST in annotations
        # ISO-8601 UTC — sanity-check the format
        assert annotations[ANNOTATION_DEPLOYED_AT].endswith("+00:00")

    def test_no_annotations_when_neither_supplied(
        self,
        builder,
        metadata,
        transformed_compose,
        connection,
    ):
        # If we have nothing meaningful to attach, don't carry an empty dict
        # — but `deployed-at` is always present, since "when" is always known.
        payload = builder.build(
            metadata,
            transformed_compose,
            connection,
            "my-app-dev-abc",
        )
        annotations = (payload.model_extra or {}).get("annotations")
        # build_annotations always sets `deployed-at`, so the key exists even
        # without a deployer/revision. Verify deployer/revision are absent.
        assert annotations is not None
        assert ANNOTATION_DEPLOYER not in annotations
        assert ANNOTATION_REVISION not in annotations
        assert ANNOTATION_DEPLOYED_AT in annotations

    def test_build_annotations_drops_empty_values(self):
        out = PayloadBuilder.build_annotations(deployer=None, revision=None)
        assert ANNOTATION_DEPLOYER not in out
        assert ANNOTATION_REVISION not in out
        assert ANNOTATION_DEPLOYED_AT in out


class TestVerifySslPropagation:
    """The deployed extension's ``tlsRejectUnauthorized`` must reflect
    the developer's intent. Three independent inputs collapse here via
    ``ConnectionInfo.effective_verify_ssl``:
    1. ``KAMIWAZA_VERIFY_SSL`` env var (per-session override)
    2. URL hostname (dev TLDs auto-disable)
    3. Persisted ``connection.verify_ssl`` from ``kz-ext login``
    """

    def test_env_false_overrides_connection_verify_true(
        self,
        builder,
        metadata,
        transformed_compose,
        connection,
        monkeypatch,
    ):
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
        assert connection.verify_ssl is True

        payload = builder.build(metadata, transformed_compose, connection, "ext")

        assert payload.kamiwaza.tls_reject_unauthorized == "0"
        primary_env = next(s for s in payload.services if s.primary).env or []
        assert {"name": "KAMIWAZA_VERIFY_SSL", "value": "false"} in primary_env

    def test_dev_tld_auto_disables_verify(
        self,
        builder,
        metadata,
        transformed_compose,
        monkeypatch,
    ):
        """User logged in normally (verify_ssl=True) against
        ``kamiwaza.test`` — should still ship ``tls_reject="0"`` because
        ``.test`` URLs always use self-signed certs."""
        monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
        conn = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
            verify_ssl=True,  # persisted strict — auto-disable should win
        )

        payload = builder.build(metadata, transformed_compose, conn, "ext")
        assert payload.kamiwaza.tls_reject_unauthorized == "0"

    def test_env_true_re_enables_against_dev_tld(
        self,
        builder,
        metadata,
        transformed_compose,
        monkeypatch,
    ):
        """``KAMIWAZA_VERIFY_SSL=true`` explicitly opts back in even
        for dev TLDs (e.g., user has a trusted local root CA)."""
        monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "true")
        conn = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
            verify_ssl=True,
        )

        payload = builder.build(metadata, transformed_compose, conn, "ext")
        assert payload.kamiwaza.tls_reject_unauthorized == "1"

    def test_production_url_keeps_strict(
        self,
        builder,
        metadata,
        transformed_compose,
        monkeypatch,
    ):
        """Real public hostnames keep persisted strict setting."""
        monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
        conn = ConnectionInfo(
            name="prod",
            url="https://api.kamiwaza.ai/api",
            active=True,
            created_at=0.0,
            verify_ssl=True,
        )

        payload = builder.build(metadata, transformed_compose, conn, "ext")
        assert payload.kamiwaza.tls_reject_unauthorized == "1"


class TestServiceRefRewritesAnnotation:
    """The frontend's compose ``http://backend:8000`` doesn't resolve in
    K8s DNS — bare ``backend`` only works in docker-compose. The
    operator reads ``extensions.kamiwaza.io/service-ref-rewrites`` to
    swap the env value to the deployment-prefixed K8s service name at
    deploy time."""

    def test_emitted_when_compose_has_cross_service_url(
        self,
        builder,
        metadata,
        connection,
    ):
        import json

        # Frontend references ``backend`` (a sibling service); the
        # transformer would have already resolved any ``${VAR:-default}``
        # syntax so we feed it the post-resolution form.
        transformed = {
            "services": {
                "frontend": {
                    "image": "reg/my-app-frontend:dev",
                    "ports": ["3000"],
                    "environment": {"BACKEND_URL": "http://backend:8000"},
                },
                "backend": {
                    "image": "reg/my-app-backend:dev",
                    "ports": ["8000"],
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "my-app-dev-abc")

        annotations = (payload.model_extra or {}).get("annotations") or {}
        raw = annotations.get(ANNOTATION_SERVICE_REF_REWRITES)
        assert raw is not None, "service-ref-rewrites annotation missing"
        rewrites = json.loads(raw)
        assert rewrites == {
            "frontend": {
                "BACKEND_URL": {
                    "from": "http://backend:8000",
                    "to": "http://my-app-dev-abc-backend:8000",
                }
            }
        }

    def test_omitted_when_no_cross_service_urls(
        self,
        builder,
        metadata,
        connection,
    ):
        transformed = {
            "services": {
                "frontend": {
                    "image": "reg/my-app-frontend:dev",
                    "ports": ["3000"],
                    "environment": {"FOO": "bar"},
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "my-app-dev-abc")
        annotations = (payload.model_extra or {}).get("annotations") or {}
        assert ANNOTATION_SERVICE_REF_REWRITES not in annotations


class TestEnvParsing:
    def test_list_format(self, builder):
        result = builder._parse_env(["KEY=value", "BARE_KEY"])
        assert result == [
            {"name": "KEY", "value": "value"},
            {"name": "BARE_KEY"},
        ]

    def test_dict_format(self, builder):
        result = builder._parse_env({"KEY": "value", "NULL_KEY": None})
        assert {"name": "KEY", "value": "value"} in result
        assert {"name": "NULL_KEY"} in result

    def test_empty(self, builder):
        assert builder._parse_env([]) == []
        assert builder._parse_env({}) == []


class TestDevNaming:
    def test_format(self):
        name = PayloadBuilder.make_dev_name("my-app", user_id="user-123")
        assert name.startswith("my-app-dev-")
        assert len(name.split("-")[-1]) == 6  # 6 char hash

    def test_deterministic(self):
        a = PayloadBuilder.make_dev_name("my-app", user_id="user-123")
        b = PayloadBuilder.make_dev_name("my-app", user_id="user-123")
        assert a == b

    def test_different_users_different_names(self):
        a = PayloadBuilder.make_dev_name("my-app", user_id="user-a")
        b = PayloadBuilder.make_dev_name("my-app", user_id="user-b")
        assert a != b

    def test_no_user_uses_local(self):
        name = PayloadBuilder.make_dev_name("my-app")
        assert "dev-" in name


class TestResourceParsing:
    def test_cpus_converted_to_millicpu(self, builder):
        svc = {"deploy": {"resources": {"limits": {"cpus": "0.5", "memory": "512M"}}}}
        res = builder._parse_resources(svc)
        assert res.limits["cpu"] == "500m"
        assert "cpus" not in res.limits
        assert res.limits["memory"] == "512M"

    def test_whole_cpu_converted(self, builder):
        svc = {"deploy": {"resources": {"limits": {"cpus": "2.0", "memory": "1G"}}}}
        res = builder._parse_resources(svc)
        assert res.limits["cpu"] == "2000m"

    def test_no_resources_returns_none(self, builder):
        assert builder._parse_resources({}) is None


class TestResolveType:
    def test_default_app(self, builder):
        assert builder._resolve_type({}) == "app"

    def test_template_type_takes_precedence(self, builder):
        assert builder._resolve_type({"template_type": "tool", "type": "app"}) == "tool"

    def test_service_type(self, builder):
        assert builder._resolve_type({"template_type": "service"}) == "service"


class TestHealthChecks:
    def test_frontend_uses_node_probe(
        self, builder, metadata, transformed_compose, connection
    ):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        frontend = next(s for s in payload.services if s.name == "frontend")

        health_check = frontend.model_dump()["healthCheck"]
        assert health_check["exec"]["command"][0] == "node"

    def test_generic_frontend_without_node_hints_uses_root_http_probe(
        self, builder, metadata, connection
    ):
        transformed = {
            "services": {
                "frontend": {
                    "image": "registry.test/my-app-frontend:1.0.0-dev",
                    "ports": ["8080"],
                },
            },
        }

        payload = builder.build(metadata, transformed, connection, "test")
        frontend = payload.services[0]

        health_check = frontend.model_dump()["healthCheck"]
        assert frontend.primary is True
        assert health_check["httpGet"] == {"path": "/", "port": 8080}
        assert "exec" not in health_check

    def test_frontend_port_3000_without_explicit_hints_uses_node_probe(
        self, builder, metadata, connection
    ):
        transformed = {
            "services": {
                "frontend": {
                    "image": "registry.test/my-app-frontend:1.0.0-dev",
                    "ports": ["3000"],
                },
            },
        }

        payload = builder.build(metadata, transformed, connection, "test")
        frontend = payload.services[0]

        health_check = frontend.model_dump()["healthCheck"]
        assert frontend.primary is True
        assert health_check["exec"]["command"][0] == "node"

    def test_non_frontend_primary_uses_http_probe(self, builder, metadata, connection):
        transformed = {
            "services": {
                "backend": {
                    "image": "registry.test/my-app-backend:1.0.0-dev",
                    "ports": ["8000"],
                },
            },
        }

        payload = builder.build(metadata, transformed, connection, "test")
        backend = payload.services[0]

        health_check = backend.model_dump()["healthCheck"]
        assert backend.primary is True
        assert health_check["httpGet"] == {"path": "/health", "port": 8000}
        assert "exec" not in health_check

    def test_service_type_primary_uses_root_probe(self, builder, connection):
        """ENG-3901 / F-013: the service template ships a stub
        (``python -m http.server``) that returns 404 for /health. The
        K8s startup probe loops the pod into restarts. Service-type
        primary services must probe ``/`` instead — the stub answers it,
        and developers who add real /health code can override at the
        compose level."""
        metadata = {"name": "my-svc", "version": "1.0.0", "type": "service"}
        transformed = {
            "services": {
                "service": {
                    "image": "registry.test/my-svc:1.0.0",
                    "ports": ["8000"],
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "test")
        svc = payload.services[0]
        health_check = svc.model_dump()["healthCheck"]
        assert svc.primary is True
        assert health_check["httpGet"] == {
            "path": "/",
            "port": 8000,
        }, f"service-type primary must probe / not /health; got {health_check['httpGet']!r}"

    def test_tool_type_primary_probes_sse(self, builder, connection):
        """ENG-3901 / F-013 (final): tool primary probes ``/sse`` — the
        FastMCP SSE endpoint. ``/`` 404s (FastMCP doesn't mount root) and
        ``/health`` doesn't exist on the stub. The platform API rejects
        ``tcpSocket`` and ``exec`` probe shapes with opaque 500s, so
        httpGet ``/sse`` is the only viable choice today even though the
        K8s probe may time out reading the streamed response body."""
        metadata = {"name": "my-tool", "version": "1.0.0", "type": "tool"}
        transformed = {
            "services": {
                "tool": {
                    "image": "registry.test/my-tool:1.0.0",
                    "ports": ["8080"],
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "test")
        tool = payload.services[0]
        health_check = tool.model_dump()["healthCheck"]
        assert tool.primary is True
        assert health_check["httpGet"] == {"path": "/sse", "port": 8080}
        assert "tcpSocket" not in health_check
        assert "exec" not in health_check

    def test_app_type_backend_keeps_health_probe(self, builder, connection):
        """Regression guard: F-013's fix is scoped to service/tool primary.
        The app-type backend (non-primary in app extensions, ships a real
        FastAPI /health route) must still probe /health."""
        metadata = {"name": "my-app", "version": "1.0.0", "type": "app"}
        transformed = {
            "services": {
                "frontend": {
                    "image": "registry.test/my-app-frontend:1.0.0",
                    "ports": ["3000"],
                },
                "backend": {
                    "image": "registry.test/my-app-backend:1.0.0",
                    "ports": ["8000"],
                },
            },
        }
        payload = builder.build(metadata, transformed, connection, "test")
        backend = next(s for s in payload.services if s.name == "backend")
        health_check = backend.model_dump()["healthCheck"]
        assert health_check["httpGet"] == {"path": "/health", "port": 8000}
