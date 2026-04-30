"""Tests for ComposeTransformer."""

import pytest

from kamiwaza_extensions.compose_transformer import ComposeTransformer


@pytest.fixture
def transformer():
    return ComposeTransformer()


@pytest.fixture
def multi_service_compose():
    """A realistic multi-service compose dict."""
    return {
        "services": {
            "frontend": {
                "build": {"context": ".", "dockerfile": "frontend/Dockerfile"},
                "ports": ["3000:3000"],
                "environment": ["NEXT_PUBLIC_API_URL=http://backend:8000"],
                "networks": ["default"],
            },
            "backend": {
                "build": "./backend",
                "ports": ["8000:8000"],
                "volumes": [
                    "./data:/app/data",
                    "backend_data:/app/persist",
                ],
                "environment": {
                    "OPENAI_BASE_URL": "${KAMIWAZA_ENDPOINT:-http://host.docker.internal:8080}",
                },
                "extra_hosts": ["host.docker.internal:host-gateway"],
                "container_name": "my-backend",
            },
            "db": {
                "image": "postgres:15",
                "ports": ["5432:5432"],
                "volumes": ["pgdata:/var/lib/postgresql/data"],
                "environment": {"POSTGRES_PASSWORD": "secret"},
            },
        },
        "volumes": {
            "backend_data": None,
            "pgdata": None,
        },
        "networks": {
            "default": None,
        },
    }


class TestStripHostPorts:
    def test_strips_host_port(self, transformer):
        compose = {"services": {"web": {"ports": ["3000:3000"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["web"]["ports"] == ["3000"]

    def test_strips_with_protocol(self, transformer):
        compose = {"services": {"web": {"ports": ["8080:3000/tcp"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["web"]["ports"] == ["3000/tcp"]

    def test_container_only_port_unchanged(self, transformer):
        compose = {"services": {"web": {"ports": ["8000"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["web"]["ports"] == ["8000"]


class TestStripBindMounts:
    def test_strips_relative_bind_mount(self, transformer):
        compose = {"services": {"web": {"volumes": ["./data:/app/data", "named:/app/persist"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["web"]["volumes"] == ["named:/app/persist"]

    def test_strips_absolute_bind_mount(self, transformer):
        compose = {"services": {"web": {"volumes": ["/host/path:/container"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert "volumes" not in result["services"]["web"]

    def test_keeps_named_volumes(self, transformer):
        compose = {"services": {"web": {"volumes": ["data:/app/data"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert result["services"]["web"]["volumes"] == ["data:/app/data"]


class TestBuildContextRemoval:
    def test_removes_build_adds_image(self, transformer):
        compose = {"services": {"api": {"build": "./backend"}}}
        result = transformer.transform(compose, "my-app", "1.0.0-dev", "registry.test")
        svc = result["services"]["api"]
        assert "build" not in svc
        assert svc["image"] == "registry.test/my-app-api:1.0.0-dev"

    def test_updates_existing_image_tag(self, transformer):
        compose = {
            "services": {
                "api": {
                    "build": "./backend",
                    "image": "kamiwazaai/my-app-api:old-tag",
                }
            }
        }
        result = transformer.transform(compose, "my-app", "1.0.0-dev", "registry.test")
        # When service has both build and image, use consistent registry format (matches image builder)
        assert result["services"]["api"]["image"] == "registry.test/my-app-api:1.0.0-dev"

    def test_dict_build_config(self, transformer):
        compose = {
            "services": {
                "web": {
                    "build": {"context": ".", "dockerfile": "frontend/Dockerfile"}
                }
            }
        }
        result = transformer.transform(compose, "my-app", "v1", "reg")
        assert "build" not in result["services"]["web"]
        assert result["services"]["web"]["image"] == "reg/my-app-web:v1"


class TestExternalImages:
    def test_postgres_preserved(self, transformer):
        compose = {"services": {"db": {"image": "postgres:15"}}}
        result = transformer.transform(compose, "my-app", "v1", "reg")
        assert result["services"]["db"]["image"] == "postgres:15"

    def test_redis_preserved(self, transformer):
        compose = {"services": {"cache": {"image": "redis:7-alpine"}}}
        result = transformer.transform(compose, "my-app", "v1", "reg")
        assert result["services"]["cache"]["image"] == "redis:7-alpine"


class TestNonBuildableInternalImages:
    """Pattern C — services with ``image:`` but no ``build:`` whose image
    name happens to contain the extension name (e.g. a helper image
    published from a separate repo). publish does not own these images;
    their declared tag must pass through verbatim. (ENG-3591: prior to the
    fix, the override tag was applied here too, pointing the catalog at a
    tag publish never built or pushed.)"""

    def test_internal_named_helper_preserved(self, transformer):
        compose = {
            "services": {
                "helper": {"image": "kamiwazaai/my-app-helper:0.5.0"},
            },
        }
        result = transformer.transform(
            compose, "my-app", "1.0.0-dev-abc1234", "kamiwazaai",
        )
        # Tag preserved verbatim despite the SHA-pinned revision_tag.
        assert (
            result["services"]["helper"]["image"]
            == "kamiwazaai/my-app-helper:0.5.0"
        )

    def test_mixed_buildable_and_pattern_c(self, transformer):
        compose = {
            "services": {
                "backend": {"build": "./backend"},
                "helper": {"image": "kamiwazaai/my-app-helper:0.5.0"},
                "db": {"image": "postgres:15"},
            },
        }
        result = transformer.transform(
            compose, "my-app", "1.0.0-dev-abc1234", "kamiwazaai",
        )
        # Buildable: rewritten with the revision tag.
        assert (
            result["services"]["backend"]["image"]
            == "kamiwazaai/my-app-backend:1.0.0-dev-abc1234"
        )
        # Pattern C: preserved verbatim.
        assert (
            result["services"]["helper"]["image"]
            == "kamiwazaai/my-app-helper:0.5.0"
        )
        # External: preserved verbatim.
        assert result["services"]["db"]["image"] == "postgres:15"


class TestResourceLimits:
    def test_adds_default_limits(self, transformer):
        compose = {"services": {"api": {"image": "my-app/api:1"}}}
        result = transformer.transform(compose, "my-app", "v1", "reg")
        limits = result["services"]["api"]["deploy"]["resources"]["limits"]
        assert limits["cpus"] == "1.0"
        assert limits["memory"] == "1G"

    def test_preserves_existing_limits(self, transformer):
        compose = {
            "services": {
                "api": {
                    "image": "my-app/api:1",
                    "deploy": {"resources": {"limits": {"cpus": "2.0", "memory": "4G"}}},
                }
            }
        }
        result = transformer.transform(compose, "my-app", "v1", "reg")
        limits = result["services"]["api"]["deploy"]["resources"]["limits"]
        assert limits["cpus"] == "2.0"

    def test_postgres_gets_smaller_limits(self, transformer):
        compose = {"services": {"db": {"image": "postgres:15"}}}
        result = transformer.transform(compose, "my-app", "v1", "reg")
        limits = result["services"]["db"]["deploy"]["resources"]["limits"]
        assert limits["cpus"] == "0.5"
        assert limits["memory"] == "512M"


class TestCleanup:
    def test_removes_extra_hosts(self, transformer):
        compose = {"services": {"api": {"extra_hosts": ["host.docker.internal:host-gateway"]}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert "extra_hosts" not in result["services"]["api"]

    def test_removes_container_name(self, transformer):
        compose = {"services": {"api": {"container_name": "my-api"}}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert "container_name" not in result["services"]["api"]

    def test_removes_networks(self, transformer):
        compose = {"services": {"api": {"networks": ["default"]}}, "networks": {"default": None}}
        result = transformer.transform(compose, "test", "v1", "reg")
        assert "networks" not in result["services"]["api"]
        assert "networks" not in result


class TestFullTransform:
    def test_multi_service(self, transformer, multi_service_compose):
        result = transformer.transform(
            multi_service_compose, "my-app", "1.0.0-dev-abc.123", "registry.test"
        )
        # Frontend
        fe = result["services"]["frontend"]
        assert "build" not in fe
        assert fe["image"] == "registry.test/my-app-frontend:1.0.0-dev-abc.123"
        assert fe["ports"] == ["3000"]
        assert "networks" not in fe

        # Backend
        be = result["services"]["backend"]
        assert "build" not in be
        assert "extra_hosts" not in be
        assert "container_name" not in be
        assert be["volumes"] == ["backend_data:/app/persist"]  # bind mount stripped

        # DB (external)
        db = result["services"]["db"]
        assert db["image"] == "postgres:15"

        # Top-level
        assert "networks" not in result
        assert "volumes" in result  # named volumes preserved

    def test_does_not_mutate_input(self, transformer):
        compose = {"services": {"api": {"build": ".", "ports": ["8000:8000"]}}}
        original_ports = list(compose["services"]["api"]["ports"])
        transformer.transform(compose, "test", "v1", "reg")
        assert compose["services"]["api"]["ports"] == original_ports
        assert "build" in compose["services"]["api"]
