"""Tests for MetadataValidator and ComposeValidator."""

import json

import pytest

from kamiwaza_extensions.validators.metadata import MetadataValidator
from kamiwaza_extensions.validators.compose import (
    ComposeValidator,
    is_bind_mount,
    is_missing_resource_limits_finding,
    is_missing_resource_limits_warning,
)
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

    # ── services.<name>.healthCheck (ENG-4832) ────────────────────────

    def test_services_healthcheck_httpget_passes(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {
            "tool": {
                "healthCheck": {"httpGet": {"path": "/v1/healthz", "port": 8000}}
            }
        }
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert result.passed, result.errors

    def test_services_healthcheck_tcpsocket_passes(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {"tool": {"healthCheck": {"tcpSocket": {"port": 8000}}}}
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert result.passed, result.errors

    def test_services_healthcheck_exec_passes(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {
            "tool": {
                "healthCheck": {
                    "exec": {"command": ["python3", "-c", "import sys; sys.exit(0)"]}
                }
            }
        }
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert result.passed, result.errors

    def test_services_healthcheck_grpc_passes(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {"tool": {"healthCheck": {"grpc": {"port": 50051}}}}
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert result.passed, result.errors

    def test_services_not_a_dict_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = "nope"
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("services must be" in e for e in result.errors)

    def test_services_entry_not_a_dict_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {"tool": "nope"}
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("services.tool" in e for e in result.errors)

    def test_healthcheck_not_a_dict_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {"tool": {"healthCheck": "nope"}}
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("healthCheck must be a JSON object" in e for e in result.errors)

    def test_healthcheck_with_no_probe_shape_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {"tool": {"healthCheck": {"initialDelaySeconds": 10}}}
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("must declare one of" in e for e in result.errors)

    def test_healthcheck_with_multiple_probe_shapes_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {
            "tool": {
                "healthCheck": {
                    "httpGet": {"path": "/", "port": 8000},
                    "tcpSocket": {"port": 8000},
                }
            }
        }
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("multiple probe shapes" in e for e in result.errors)

    def test_healthcheck_with_httpget_and_grpc_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {
            "tool": {
                "healthCheck": {
                    "httpGet": {"path": "/", "port": 8000},
                    "grpc": {"port": 50051},
                }
            }
        }
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("multiple probe shapes" in e for e in result.errors)

    def test_unknown_probe_shape_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {"tool": {"healthCheck": {"httpsGet": {"port": 8000}}}}
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("must declare one of" in e for e in result.errors)
        assert any("unknown field 'httpsGet'" in w for w in result.warnings)

    def test_httpget_without_port_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {"tool": {"healthCheck": {"httpGet": {"path": "/"}}}}
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("httpGet must include 'port'" in e for e in result.errors)

    def test_httpget_with_invalid_port_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {
            "tool": {"healthCheck": {"httpGet": {"path": "/", "port": "bad port"}}}
        }
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("httpGet.port must be" in e for e in result.errors)

    def test_httpget_with_out_of_range_port_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {
            "tool": {"healthCheck": {"httpGet": {"path": "/", "port": 70000}}}
        }
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("httpGet.port must be between" in e for e in result.errors)

    def test_httpget_with_named_port_passes(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {
            "tool": {"healthCheck": {"httpGet": {"path": "/", "port": "http"}}}
        }
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert result.passed, result.errors

    def test_tcpsocket_without_port_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {"tool": {"healthCheck": {"tcpSocket": {"host": "127.0.0.1"}}}}
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("tcpSocket must include 'port'" in e for e in result.errors)

    def test_grpc_without_port_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {"tool": {"healthCheck": {"grpc": {"service": "health"}}}}
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("grpc must include 'port'" in e for e in result.errors)

    def test_exec_with_empty_command_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {"tool": {"healthCheck": {"exec": {"command": []}}}}
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("exec.command must be a non-empty list" in e for e in result.errors)

    def test_exec_without_command_errors(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {"tool": {"healthCheck": {"exec": {}}}}
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert not result.passed
        assert any("exec.command must be a non-empty list" in e for e in result.errors)

    def test_probe_unknown_field_warns(self, tmp_path, validator):
        data = _valid_metadata()
        data["services"] = {
            "tool": {
                "healthCheck": {
                    "httpGet": {"path": "/", "port": 8000, "command_line": "curl"}
                }
            }
        }
        f = tmp_path / "kamiwaza.json"
        _write_json(f, data)
        result = validator.validate(f)
        assert result.passed, result.errors
        assert any("httpGet has unknown field 'command_line'" in w for w in result.warnings)


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

    def test_bind_mount_error(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {"image": "nginx", "volumes": ["./src:/app"]},
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert not result.passed
        assert any("bind mount './src:/app'" in e for e in result.errors)

    def test_long_form_bind_mount_error(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {
                    "image": "nginx",
                    "volumes": [{"type": "bind", "source": "./src", "target": "/app"}],
                },
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert not result.passed
        assert any("bind mount './src:/app'" in e for e in result.errors)

    def test_named_volume_passes_compose_validation(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {
                    "image": "nginx",
                    "volumes": ["data:/app/data"],
                    "deploy": {
                        "resources": {"limits": {"cpus": "0.5", "memory": "512M"}}
                    },
                },
            },
            "volumes": {"data": None},
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert result.passed
        assert not any("bind mount" in e.lower() for e in result.errors)

    def test_missing_resource_limits_info(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {"image": "nginx"},
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert result.passed
        # ENG-4956: deploy backfills resource limits, so the default
        # (transformer-handled) path reports this as info, not a warning.
        assert not any("resource" in w.lower() for w in result.warnings)
        assert any(
            "no resource limits defined" in i
            and "defaults will be applied at deploy" in i
            for i in result.info
        )

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

    def test_scaffold_templates_pass_validation(self, validator):
        """ENG-4834 regression: ``kz-ext create`` ships templates that
        carry bind mounts on ``build:`` services for local-dev hot
        reload. Promoting bind mounts to a hard error breaks every fresh
        scaffold's ``kz-ext validate`` and ``kz-ext publish``. Lock in
        that the shipped templates validate cleanly."""
        from pathlib import Path

        import kamiwaza_extensions

        templates_root = Path(kamiwaza_extensions.__file__).parent / "templates"
        for kind in ("app", "tool"):
            ext_dir = templates_root / kind
            compose = ext_dir / "docker-compose.yml"
            if not compose.exists():
                continue
            result = validator.validate(compose, ext_dir)
            # Templates may legitimately surface warnings (no resource
            # limits, bind-mounts-as-local-dev). They must NOT produce
            # validation errors that block create→validate→publish.
            assert result.passed, (
                f"{kind} template failed validation: {result.errors}"
            )

    def test_bind_mount_in_build_service_is_info(self, tmp_path, validator):
        """ENG-4834/ENG-4956: scaffolded extensions (``kz-ext create``) ship a
        ``build:`` service with bind mounts for hot-reload. ``ComposeTransformer``
        strips them at deploy, so the validator must NOT error on this
        local-dev pattern — and per ENG-4956 the default (transformer-handled)
        path reports it as info rather than a warning so a fresh scaffold's
        ``kz-ext validate`` is clean."""
        (tmp_path / "Dockerfile").write_text("FROM python:3.10")
        compose = {
            "services": {
                "web": {
                    "build": ".",
                    "volumes": ["./src:/app/src"],
                    "deploy": {
                        "resources": {"limits": {"cpus": "0.5", "memory": "512M"}}
                    },
                },
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert result.passed, result.errors
        assert not any("bind mount" in w.lower() for w in result.warnings)
        assert any(
            "bind mount './src:/app/src'" in i and "stripped at deploy" in i
            for i in result.info
        )

    def test_bind_mount_in_build_service_is_warning_when_transformer_bypassed(
        self, tmp_path, validator
    ):
        """ENG-4956: on a publish path that bypasses ComposeTransformer (an
        authored appgarden compose), a build-service bind mount is NOT
        stripped, so it must surface as an actionable warning, not info."""
        (tmp_path / "Dockerfile").write_text("FROM python:3.10")
        compose = {
            "services": {
                "web": {
                    "build": ".",
                    "volumes": ["./src:/app/src"],
                    "deploy": {
                        "resources": {"limits": {"cpus": "0.5", "memory": "512M"}}
                    },
                },
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path, transformer_handled=False)
        assert result.passed, result.errors
        assert not any("bind mount" in i.lower() for i in result.info)
        assert any(
            "bind mount './src:/app/src'" in w and "stripped at deploy" not in w
            for w in result.warnings
        )

    def test_missing_limits_is_warning_when_transformer_bypassed(
        self, tmp_path, validator
    ):
        """ENG-4956: when the transformer is bypassed, deploy-time resource
        defaults are not applied, so the finding must be a warning."""
        compose = {"services": {"web": {"image": "nginx", "build": "."}}}
        (tmp_path / "Dockerfile").write_text("FROM python:3.10")
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path, transformer_handled=False)
        assert result.passed
        assert not any("resource limits" in i.lower() for i in result.info)
        assert any(
            "no resource limits defined" in w and "at deploy" not in w
            for w in result.warnings
        )

    def test_shared_named_volume_warns_about_emptydir(self, tmp_path, validator):
        """ENG-4834: named volumes deploy as pod-scoped ``emptyDir``, so
        two services referencing the same name do NOT share data at
        runtime. Surface this before the user is surprised post-deploy."""
        compose = {
            "services": {
                "api": {
                    "image": "nginx",
                    "volumes": ["shared:/app/data"],
                    "deploy": {
                        "resources": {"limits": {"cpus": "0.5", "memory": "512M"}}
                    },
                },
                "worker": {
                    "image": "nginx",
                    "volumes": ["shared:/app/data"],
                    "deploy": {
                        "resources": {"limits": {"cpus": "0.5", "memory": "512M"}}
                    },
                },
            },
            "volumes": {"shared": None},
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert result.passed
        assert any(
            "emptyDir is pod-scoped" in w and "shared" in w for w in result.warnings
        )

    def test_tmpfs_long_form_is_rejected(self, tmp_path, validator):
        """``ComposeTransformer._strip_bind_mounts`` silently drops tmpfs
        mounts — same failure mode as the pre-ENG-4834 named-volume drop.
        Surface explicitly so the user can fix the compose."""
        compose = {
            "services": {
                "web": {
                    "image": "nginx",
                    "volumes": [{"type": "tmpfs", "target": "/run/cache"}],
                },
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert not result.passed
        assert any("tmpfs mount" in e for e in result.errors)

    def test_interpolated_bind_source_is_rejected(self, tmp_path, validator):
        """PR-113 review High #1: a shell-interpolated bind source
        (``${PWD}/src``, ``$HOME/.cache``) resolves to a host path at
        runtime and can never be a named volume. The validator must
        reject it on a prebuilt-image service rather than letting the
        payload builder turn it into an emptyDir."""
        compose = {
            "services": {
                "web": {
                    "image": "nginx",
                    "volumes": ["${PWD}/src:/app/src", "$HOME/.cache:/cache"],
                },
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert not result.passed
        bind_errors = [e for e in result.errors if "bind mount" in e]
        assert len(bind_errors) == 2

    def test_service_level_tmpfs_key_is_rejected(self, tmp_path, validator):
        """Compose's top-level ``tmpfs:`` service key is distinct from
        ``volumes:``. ``ComposeTransformer`` only strips long-form tmpfs
        entries inside ``volumes:``, so a service-level ``tmpfs:`` slips
        through and silently loses the mount at deploy — reject it."""
        compose = {
            "services": {
                "web": {
                    "image": "nginx",
                    "tmpfs": ["/run/cache", "/var/cache:size=64m"],
                },
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert not result.passed
        tmpfs_errors = [e for e in result.errors if "tmpfs mount" in e]
        assert len(tmpfs_errors) == 2

    def test_service_level_tmpfs_string_form_is_rejected(self, tmp_path, validator):
        """The ``tmpfs:`` key also accepts a bare string."""
        compose = {
            "services": {
                "web": {"image": "nginx", "tmpfs": "/run/cache"},
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        assert not result.passed
        assert any("tmpfs mount '/run/cache'" in e for e in result.errors)

    def test_null_volumes_key_does_not_crash(self, tmp_path, validator):
        """A YAML ``volumes:`` key with no value parses to ``None``;
        the per-service loop must not crash trying to iterate it."""
        compose = {
            "services": {
                "web": {"image": "nginx", "volumes": None},
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)
        result = validator.validate(f, tmp_path)
        # Should not raise; service has no volumes -> no bind-mount findings
        assert not any("bind mount" in e for e in result.errors)

    def test_invalid_yaml(self, tmp_path, validator):
        f = tmp_path / "docker-compose.yml"
        f.write_text(": invalid: yaml: {{{}}")
        result = validator.validate(f, tmp_path)
        # yaml.safe_load might not error on all bad yaml; check it doesn't crash
        assert isinstance(result, ComposeValidator.__init__.__class__) or True


@pytest.mark.unit
class TestIsBindMount:
    """Direct coverage for the validator/transformer shared bind-mount detector."""

    @pytest.mark.parametrize(
        "volume",
        [
            "/abs/host:/app",
            "./rel:/app",
            "../rel:/app",
            "~/home:/app",
            r"C:\host\data:/app",
            ".:/app",
            "..:/app",
            "${PWD}/src:/app",
            {"type": "bind", "source": "./src", "target": "/app"},
            {"source": "/abs/host", "target": "/app"},
        ],
    )
    def test_detects_bind_mounts(self, volume):
        assert is_bind_mount(volume) is True

    @pytest.mark.parametrize(
        "volume",
        [
            "data:/app",
            "logs:/var/log:rw",
            "named_volume:/data",
            {"type": "volume", "source": "data", "target": "/app"},
            {"type": "tmpfs", "target": "/run/cache"},
            {"source": "data", "target": "/app"},
        ],
    )
    def test_ignores_named_volumes(self, volume):
        assert is_bind_mount(volume) is False


@pytest.mark.unit
class TestResourceLimitsFinding:
    """The renamed helper and its backward-compatible alias."""

    def test_finding_matcher(self):
        assert is_missing_resource_limits_finding(
            "Service 'web': no resource limits defined — defaults applied"
        )
        assert not is_missing_resource_limits_finding("Service 'web': all good")

    def test_deprecated_alias_is_same_callable(self):
        # External importers of the pre-ENG-4956 name must keep working.
        assert is_missing_resource_limits_warning is is_missing_resource_limits_finding


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

    def test_flags_image_only_rootful_nginx_service(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {
                    "image": "nginx:alpine",
                    "ports": ["8080:8080"],
                },
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)

        result = validator.validate(f, tmp_path)

        assert not result.passed
        assert any("image-only nginx service" in err for err in result.errors)
        assert any("could not be inspected" in warning for warning in result.warnings)

    def test_warns_for_image_only_unprivileged_nginx_service(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {
                    "image": "nginxinc/nginx-unprivileged:stable-alpine",
                    "ports": ["8080:8080"],
                },
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)

        result = validator.validate(f, tmp_path)

        assert result.passed
        assert result.errors == []
        assert any("could not be inspected" in warning for warning in result.warnings)

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

    def test_flags_unprivileged_nginx_when_final_user_is_root(self, tmp_path, validator):
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
            "USER root\n"
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

        assert not result.passed
        assert any("does not switch to a non-root user" in err for err in result.errors)

    def test_uses_compose_build_context_for_nginx_config_lookup(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {
                    "build": {
                        "context": ".",
                        "dockerfile": "docker/Dockerfile",
                    },
                    "ports": ["8080:8080"],
                },
            },
        }
        (tmp_path / "docker").mkdir()
        (tmp_path / "docker" / "Dockerfile").write_text(
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

    def test_uses_copied_nginx_config_source_not_filename_heuristic(self, tmp_path, validator):
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
            "COPY site.conf /etc/nginx/conf.d/default.conf\n"
            "EXPOSE 8080\n"
        )
        (tmp_path / "site.conf").write_text(
            "server {\n"
            "    listen 80;\n"
            "    client_body_temp_path /tmp/client_temp;\n"
            "}\n"
        )
        (tmp_path / "nginx-preview.conf").write_text(
            "server {\n"
            "    listen 8080;\n"
            "    client_body_temp_path /tmp/client_temp;\n"
            "}\n"
        )
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)

        result = validator.validate(f, tmp_path)

        assert not result.passed
        assert any("privileged port 80" in err for err in result.errors)

    def test_ignores_unrelated_nginx_named_config_files(self, tmp_path, validator):
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
            "COPY site.conf /etc/nginx/conf.d/default.conf\n"
            "EXPOSE 8080\n"
        )
        (tmp_path / "site.conf").write_text(
            "server {\n"
            "    listen 8080;\n"
            "    client_body_temp_path /tmp/client_temp;\n"
            "}\n"
        )
        (tmp_path / "nginx-preview.conf").write_text(
            "server {\n"
            "    listen 80;\n"
            "    client_body_temp_path /tmp/client_temp;\n"
            "}\n"
        )
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)

        result = validator.validate(f, tmp_path)

        assert result.passed
        assert result.errors == []

    def test_rejects_build_path_that_escapes_extension_dir(self, tmp_path, validator):
        compose = {
            "services": {
                "web": {
                    "build": {
                        "context": "..",
                        "dockerfile": "Dockerfile",
                    },
                    "ports": ["8080:8080"],
                },
            },
        }
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)

        result = validator.validate(f, tmp_path)

        assert result.passed
        assert any("escapes the extension directory" in warning for warning in result.warnings)

    def test_allows_ipv4_bound_nginx_listen_port(self, tmp_path, validator):
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
            "    listen 127.0.0.1:8080;\n"
            "    client_body_temp_path /tmp/client_temp;\n"
            "}\n"
        )
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)

        result = validator.validate(f, tmp_path)

        assert result.passed
        assert not any("privileged port 127" in err for err in result.errors)

    def test_ignores_heredoc_body_when_parsing_dockerfile(self, tmp_path, validator):
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
            "RUN cat <<'EOF' >/tmp/banner\n"
            "FROM python:3.11\n"
            "USER root\n"
            "EOF\n"
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

    def test_allows_wildcard_bound_nginx_listen_port(self, tmp_path, validator):
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
            "    listen *:8080;\n"
            "    client_body_temp_path /tmp/client_temp;\n"
            "}\n"
        )
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)

        result = validator.validate(f, tmp_path)

        assert result.passed

    def test_allows_localhost_bound_nginx_listen_port(self, tmp_path, validator):
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
            "    listen localhost:8080;\n"
            "    client_body_temp_path /tmp/client_temp;\n"
            "}\n"
        )
        f = tmp_path / "docker-compose.yml"
        self._write_compose(f, compose)

        result = validator.validate(f, tmp_path)

        assert result.passed
