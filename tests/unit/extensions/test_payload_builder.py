"""Tests for PayloadBuilder."""

import pytest

from kamiwaza_extensions.connections import ConnectionInfo
from kamiwaza_extensions.payload_builder import PayloadBuilder


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
    def test_produces_valid_payload(self, builder, metadata, transformed_compose, connection):
        payload = builder.build(metadata, transformed_compose, connection, "my-app-dev-abc123")
        assert payload.name == "my-app-dev-abc123"
        assert payload.type == "app"
        assert payload.version == "1.0.0"
        assert len(payload.services) == 2

    def test_primary_service_assigned(self, builder, metadata, transformed_compose, connection):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        primaries = [s for s in payload.services if s.primary]
        assert len(primaries) == 1
        assert primaries[0].name == "frontend"

    def test_ports_parsed(self, builder, metadata, transformed_compose, connection):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        fe = next(s for s in payload.services if s.name == "frontend")
        assert len(fe.ports) == 1
        assert fe.ports[0].container_port == 3000

    def test_kamiwaza_integration(self, builder, metadata, transformed_compose, connection):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        assert payload.kamiwaza.api_url == "https://cluster.kamiwaza.test/api"
        assert payload.kamiwaza.use_auth == "true"

    def test_security_from_metadata(self, builder, metadata, transformed_compose, connection):
        payload = builder.build(metadata, transformed_compose, connection, "test")
        assert payload.security.risk_tier == 1


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
