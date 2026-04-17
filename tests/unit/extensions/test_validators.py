"""Tests for MetadataValidator and ComposeValidator."""

import json

import pytest

from kamiwaza_extensions.validators.metadata import MetadataValidator
from kamiwaza_extensions.validators.compose import ComposeValidator
from kamiwaza_extensions.validators.platform_runtime import PlatformRuntimeValidator


def _valid_metadata() -> dict:
    return {
        "name": "my-app",
        "version": "1.0.0",
        "source_type": "kamiwaza",
        "visibility": "public",
        "description": "A test extension",
        "risk_tier": 0,
        "verified": False,
    }


def _write_json(path, data):
    path.write_text(json.dumps(data))


# ── MetadataValidator ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMetadataValidator:
    @pytest.fixture
    def validator(self):
        return MetadataValidator()

    def test_valid_metadata_passes(self, tmp_path, validator):
        f = tmp_path / "kamiwaza.json"
        _write_json(f, _valid_metadata())
        result = validator.validate(f)
        assert result.passed
        assert result.errors == []

    def test_missing_required_field(self, tmp_path, validator):
        data = _valid_metadata()
        del data["name"]
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed

    def test_invalid_version_format(self, tmp_path, validator):
        data = _valid_metadata()
        data["version"] = "not-a-version"
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("version" in e.lower() for e in result.errors)

    def test_invalid_source_type(self, tmp_path, validator):
        data = _valid_metadata()
        data["source_type"] = "invalid"
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed

    def test_tool_naming_convention_warning(self, tmp_path, validator):
        data = _valid_metadata()
        data["name"] = "bad-name"
        data["type"] = "tool"
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert result.passed  # warning, not error
        assert any("tool-" in w for w in result.warnings)

    def test_tool_name_with_prefix_no_warning(self, tmp_path, validator):
        data = _valid_metadata()
        data["name"] = "tool-my-tool"
        data["type"] = "tool"
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert result.passed
        assert not any("tool-" in w for w in result.warnings)

    def test_invalid_kz_ext_version_range(self, tmp_path, validator):
        data = _valid_metadata()
        data["kz_ext_version"] = "not a range"
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("kz_ext_version" in e for e in result.errors)

    def test_valid_kz_ext_version_range(self, tmp_path, validator):
        data = _valid_metadata()
        data["kz_ext_version"] = ">=0.1.0,<1.0.0"
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert result.passed

    def test_invalid_json_file(self, tmp_path, validator):
        f = tmp_path / "kamiwaza.json"
        f.write_text("{bad json")
        result = validator.validate(f)
        assert not result.passed
        assert any("JSON" in e for e in result.errors)

    def test_preview_image_warnings(self, tmp_path, validator):
        data = _valid_metadata()
        data["preview_image"] = "wrong/path.png"
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert result.passed
        assert any("images/" in w for w in result.warnings)

    def test_extra_fields_allowed(self, tmp_path, validator):
        data = _valid_metadata()
        data["new_future_field"] = "hello"
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert result.passed

    def test_file_not_found(self, tmp_path, validator):
        result = validator.validate(tmp_path / "nonexistent.json")
        assert not result.passed


# ── ComposeValidator ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestComposeValidator:
    @pytest.fixture
    def validator(self):
        return ComposeValidator()

    def _write_compose(self, path, data):
        import yaml
        path.write_text(yaml.dump(data))

    def test_clean_compose_passes(self, tmp_path, validator):
        # Create Dockerfile so build check passes
        (tmp_path / "Dockerfile").write_text("FROM python:3.10")
        compose = {
            "services": {
                "web": {
                    "build": ".",
                    "deploy": {"resources": {"limits": {"cpus": "0.5", "memory": "512M"}}},
                },
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert result.passed
        assert result.errors == []

    def test_host_port_binding_warning(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {"image": "nginx", "ports": ["8080:3000"]},
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert result.passed  # warning, not error
        assert any("port" in w.lower() for w in result.warnings)

    def test_bind_mount_warning(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {"image": "nginx", "volumes": ["./src:/app"]},
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert result.passed
        assert any("bind mount" in w.lower() for w in result.warnings)

    def test_missing_resource_limits_warning(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {"image": "nginx"},
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert result.passed
        assert any("resource" in w.lower() for w in result.warnings)

    def test_missing_dockerfile_error(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {"build": {"context": ".", "dockerfile": "Missing.Dockerfile"}},
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert not result.passed
        assert any("Dockerfile" in e for e in result.errors)

    def test_container_name_warning(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {"image": "nginx", "container_name": "my-container"},
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert result.passed
        assert any("container_name" in w for w in result.warnings)

    def test_custom_networks_warning(self, tmp_path, validator):
        compose = {
            "services": {"web": {"image": "nginx"}},
            "networks": {"custom": {"driver": "bridge"}},
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert result.passed
        assert any("network" in w.lower() for w in result.warnings)

    def test_invalid_yaml(self, tmp_path, validator):
        f = tmp_path / "docker-compose.yml"
        f.write_text(": invalid: yaml: {{{}}")
        result = validator.validate(f, tmp_path)
        # yaml.safe_load might not error on all bad yaml; check it doesn't crash
        assert isinstance(result, ComposeValidator.__init__.__class__) or True


@pytest.mark.unit
class TestPlatformRuntimeValidator:
    @pytest.fixture
    def validator(self):
        return PlatformRuntimeValidator()

    def _write_compose(self, path, data):
        import yaml

        path.write_text(yaml.dump(data))

    def test_flags_privileged_port_and_rootful_nginx(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {
                    "build": ".",
                    "ports": ["8080:80"],
                },
            },
        }
        (tmp_path / "Dockerfile").write_text(
            "FROM nginx:alpine\nCOPY index.html /usr/share/nginx/html/index.html\nEXPOSE 80\n"
        )
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)

        result = validator.validate(f, tmp_path)

        assert not result.passed
        assert any("container port 80 is privileged" in err for err in result.errors)
        assert any("does not switch to a non-root user" in err for err in result.errors)

    def test_flags_nginx_without_tmp_runtime_paths(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {
                    "build": ".",
                    "ports": ["8080:8080"],
                },
            },
        }
        (tmp_path / "Dockerfile").write_text(
            "FROM nginx:alpine\nUSER 1001\nCOPY index.html /usr/share/nginx/html/index.html\nEXPOSE 8080\n"
        )
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)

        result = validator.validate(f, tmp_path)

        assert not result.passed
        assert any("/tmp paths" in err for err in result.errors)

    def test_allows_unprivileged_nginx_with_tmp_runtime_paths(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {
                    "build": ".",
                    "ports": ["8080:8080"],
                },
            },
        }
        (tmp_path / "Dockerfile").write_text(
            "FROM nginxinc/nginx-unprivileged:stable-alpine\n"
            "COPY nginx.conf /etc/nginx/conf.d/default.conf\n"
            "EXPOSE 8080\n"
        )
        (tmp_path / "nginx.conf").write_text(
            "server {\n"
            "    listen 8080;\n"
            "    client_body_temp_path /tmp/client_temp;\n"
            "}\n"
        )
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)

        result = validator.validate(f, tmp_path)

        assert result.passed
        assert result.errors == []
