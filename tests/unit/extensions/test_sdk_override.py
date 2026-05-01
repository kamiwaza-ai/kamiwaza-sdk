"""Tests for SDK override config, validation, and compose generation."""

import pytest
import yaml

from kamiwaza_extensions.sdk_override import (
    BuildOverride,
    SdkOverrideSpec,
    apply_build_overlay,
    detect_service_type,
    generate_build_overrides,
    generate_compose_override,
    resolve_sdk_override,
    validate_sdk_override,
)

# ------------------------------------------------------------------
# SdkOverrideSpec
# ------------------------------------------------------------------


@pytest.mark.unit
class TestSdkOverrideSpec:
    def test_paths(self, tmp_path):
        spec = SdkOverrideSpec(sdk_repo=tmp_path)
        assert spec.python_lib_path == tmp_path / "kamiwaza_extensions_lib"
        assert spec.typescript_lib_path == tmp_path / "kamiwaza-ai-extensions-lib"
        assert (
            spec.typescript_dist_path
            == tmp_path / "kamiwaza-ai-extensions-lib" / "dist"
        )

    def test_defaults(self, tmp_path):
        spec = SdkOverrideSpec(sdk_repo=tmp_path)
        assert spec.python is True
        assert spec.typescript is True
        assert spec.build_typescript is False


# ------------------------------------------------------------------
# resolve_sdk_override
# ------------------------------------------------------------------


@pytest.mark.unit
class TestResolveSdkOverride:
    def test_cli_flag(self, tmp_path):
        spec = resolve_sdk_override(str(tmp_path), extension_path=tmp_path)
        assert spec is not None
        assert spec.sdk_repo == tmp_path

    def test_cli_flag_with_tilde(self, tmp_path):
        # Ensure ~ expansion works
        spec = resolve_sdk_override("~/nonexistent-path-12345", extension_path=tmp_path)
        assert spec is not None
        assert "~" not in str(spec.sdk_repo)

    def test_cli_flag_overrides_config(self, tmp_path):
        # Create config with different path
        config_dir = tmp_path / ".kz-ext"
        config_dir.mkdir()
        (config_dir / "local.yaml").write_text(yaml.dump({"sdk_repo": "/other/path"}))
        spec = resolve_sdk_override(str(tmp_path), extension_path=tmp_path)
        assert spec is not None
        assert spec.sdk_repo == tmp_path  # CLI wins

    def test_config_file(self, tmp_path):
        config_dir = tmp_path / ".kz-ext"
        config_dir.mkdir()
        (config_dir / "local.yaml").write_text(yaml.dump({"sdk_repo": str(tmp_path)}))
        spec = resolve_sdk_override(None, extension_path=tmp_path)
        assert spec is not None
        assert spec.sdk_repo == tmp_path

    def test_config_file_selective_libs(self, tmp_path):
        config_dir = tmp_path / ".kz-ext"
        config_dir.mkdir()
        (config_dir / "local.yaml").write_text(
            yaml.dump(
                {
                    "sdk_repo": str(tmp_path),
                    "runtime_libs": {"python": "local", "typescript": "published"},
                    "build_typescript": True,
                }
            )
        )
        spec = resolve_sdk_override(None, extension_path=tmp_path)
        assert spec is not None
        assert spec.python is True
        assert spec.typescript is False
        assert spec.build_typescript is True

    def test_no_config_returns_none(self, tmp_path):
        spec = resolve_sdk_override(None, extension_path=tmp_path)
        assert spec is None

    def test_empty_config_returns_none(self, tmp_path):
        config_dir = tmp_path / ".kz-ext"
        config_dir.mkdir()
        (config_dir / "local.yaml").write_text("")
        spec = resolve_sdk_override(None, extension_path=tmp_path)
        assert spec is None

    def test_config_without_sdk_repo_returns_none(self, tmp_path):
        config_dir = tmp_path / ".kz-ext"
        config_dir.mkdir()
        (config_dir / "local.yaml").write_text(yaml.dump({"build_typescript": True}))
        spec = resolve_sdk_override(None, extension_path=tmp_path)
        assert spec is None

    def test_invalid_yaml_returns_none(self, tmp_path):
        config_dir = tmp_path / ".kz-ext"
        config_dir.mkdir()
        (config_dir / "local.yaml").write_text(": invalid: yaml: [")
        spec = resolve_sdk_override(None, extension_path=tmp_path)
        assert spec is None


# ------------------------------------------------------------------
# validate_sdk_override
# ------------------------------------------------------------------


@pytest.mark.unit
class TestValidateSdkOverride:
    def test_valid_repo(self, tmp_path):
        import time

        (tmp_path / "kamiwaza_extensions_lib").mkdir()
        ts_lib = tmp_path / "kamiwaza-ai-extensions-lib"
        ts_lib.mkdir()
        (ts_lib / "dist").mkdir()
        (ts_lib / "src").mkdir()
        # Make src first, then dist (dist is newer)
        (ts_lib / "src" / "index.ts").write_text("//")
        time.sleep(0.05)
        (ts_lib / "dist" / "index.js").write_text("//")

        spec = SdkOverrideSpec(sdk_repo=tmp_path)
        result = validate_sdk_override(spec)
        assert result.ok
        assert result.errors == []
        assert result.warnings == []

    def test_missing_repo(self, tmp_path):
        spec = SdkOverrideSpec(sdk_repo=tmp_path / "nonexistent")
        result = validate_sdk_override(spec)
        assert not result.ok
        assert "not found" in result.errors[0]

    def test_missing_python_lib(self, tmp_path):
        ts_lib = tmp_path / "kamiwaza-ai-extensions-lib"
        ts_lib.mkdir()
        (ts_lib / "dist").mkdir()

        spec = SdkOverrideSpec(sdk_repo=tmp_path)
        result = validate_sdk_override(spec)
        assert not result.ok
        assert "Python" in result.errors[0]

    def test_missing_ts_lib(self, tmp_path):
        (tmp_path / "kamiwaza_extensions_lib").mkdir()

        spec = SdkOverrideSpec(sdk_repo=tmp_path)
        result = validate_sdk_override(spec)
        assert not result.ok
        assert "TypeScript" in result.errors[0]

    def test_missing_ts_dist_warns(self, tmp_path):
        (tmp_path / "kamiwaza_extensions_lib").mkdir()
        ts_lib = tmp_path / "kamiwaza-ai-extensions-lib"
        ts_lib.mkdir()
        # No dist/

        spec = SdkOverrideSpec(sdk_repo=tmp_path)
        result = validate_sdk_override(spec)
        assert result.ok  # Warning, not error
        assert len(result.warnings) == 1
        assert "dist/ missing" in result.warnings[0]

    def test_stale_ts_dist_warns(self, tmp_path):
        import time

        (tmp_path / "kamiwaza_extensions_lib").mkdir()
        ts_lib = tmp_path / "kamiwaza-ai-extensions-lib"
        ts_lib.mkdir()
        (ts_lib / "dist").mkdir()
        (ts_lib / "src").mkdir()
        # dist is older
        (ts_lib / "dist" / "index.js").write_text("//")
        time.sleep(0.05)
        (ts_lib / "src" / "index.ts").write_text("// newer")

        spec = SdkOverrideSpec(sdk_repo=tmp_path)
        result = validate_sdk_override(spec)
        assert result.ok
        assert any("stale" in w for w in result.warnings)

    def test_python_only_skips_ts_validation(self, tmp_path):
        (tmp_path / "kamiwaza_extensions_lib").mkdir()

        spec = SdkOverrideSpec(sdk_repo=tmp_path, typescript=False)
        result = validate_sdk_override(spec)
        assert result.ok

    def test_ts_only_skips_python_validation(self, tmp_path):
        ts_lib = tmp_path / "kamiwaza-ai-extensions-lib"
        ts_lib.mkdir()
        (ts_lib / "dist").mkdir()

        spec = SdkOverrideSpec(sdk_repo=tmp_path, python=False)
        result = validate_sdk_override(spec)
        assert result.ok


# ------------------------------------------------------------------
# detect_service_type
# ------------------------------------------------------------------


@pytest.mark.unit
class TestDetectServiceType:
    def test_name_frontend(self):
        assert detect_service_type("frontend", {}) == "frontend"

    def test_name_ui(self):
        assert detect_service_type("ui", {}) == "frontend"

    def test_name_web(self):
        assert detect_service_type("web-app", {}) == "frontend"

    def test_name_backend(self):
        assert detect_service_type("backend", {}) == "backend"

    def test_name_api(self):
        assert detect_service_type("api", {}) == "backend"

    def test_dockerfile_path(self):
        config = {"build": {"dockerfile": "frontend/Dockerfile"}}
        assert detect_service_type("svc", config) == "frontend"

    def test_context_path(self):
        config = {"build": {"context": "./frontend"}}
        assert detect_service_type("svc", config) == "frontend"

    def test_port_3000(self):
        config = {"ports": ["3000:3000"]}
        assert detect_service_type("svc", config) == "frontend"

    def test_port_8000(self):
        config = {"ports": ["8000:8000"]}
        assert detect_service_type("svc", config) == "backend"

    def test_default_backend(self):
        assert detect_service_type("worker", {}) == "backend"


# ------------------------------------------------------------------
# generate_compose_override
# ------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateComposeOverride:
    def _make_spec(self, tmp_path, **kwargs):
        return SdkOverrideSpec(sdk_repo=tmp_path, **kwargs)

    def _make_ext_dir(self, tmp_path):
        """Create a minimal extension directory with Dockerfiles."""
        ext = tmp_path / "ext"
        ext.mkdir()
        be = ext / "backend"
        be.mkdir()
        (be / "Dockerfile").write_text(
            'FROM python:3.10\nCMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]\n'
        )
        fe = ext / "frontend"
        fe.mkdir()
        (fe / "Dockerfile").write_text(
            'FROM node:20\nENTRYPOINT ["node", "/app/start.mjs"]\n'
        )
        return ext

    def test_both_libs(self, tmp_path):
        ext_dir = self._make_ext_dir(tmp_path)
        spec = self._make_spec(tmp_path)
        compose = {
            "services": {
                "frontend": {
                    "build": {"context": "./frontend"},
                    "ports": ["3000:3000"],
                },
                "backend": {"build": {"context": "./backend"}, "ports": ["8000:8000"]},
            }
        }
        override = generate_compose_override(spec, compose, extension_dir=ext_dir)
        services = override["services"]

        # Backend reads CMD from Dockerfile
        assert (
            'export PYTHONPATH="/sdk$${PYTHONPATH:+:$$PYTHONPATH}"'
            in services["backend"]["command"][0]
        )
        assert (
            "exec uvicorn app.main:app --host 0.0.0.0"
            in services["backend"]["command"][0]
        )

        # Frontend reads ENTRYPOINT from Dockerfile
        assert "npm pack" in services["frontend"]["command"][0]
        assert "exec node /app/start.mjs" in services["frontend"]["command"][0]

    def test_combines_entrypoint_and_cmd(self, tmp_path):
        ext_dir = tmp_path / "ext"
        ext_dir.mkdir()
        frontend = ext_dir / "frontend"
        frontend.mkdir()
        (frontend / "Dockerfile").write_text(
            'FROM node:20\nENTRYPOINT ["docker-entrypoint.sh"]\n'
            'CMD ["npm", "run", "start"]\n'
        )

        spec = self._make_spec(tmp_path, python=False)
        compose = {
            "services": {
                "frontend": {
                    "build": {"context": "./frontend"},
                    "ports": ["3000:3000"],
                },
            }
        }

        override = generate_compose_override(spec, compose, extension_dir=ext_dir)
        command = override["services"]["frontend"]["command"][0]
        assert "exec docker-entrypoint.sh npm run start" in command

    def test_quotes_json_array_cmd_arguments(self, tmp_path):
        ext_dir = tmp_path / "ext"
        ext_dir.mkdir()
        backend = ext_dir / "backend"
        backend.mkdir()
        (backend / "Dockerfile").write_text(
            'FROM python:3.10\nCMD ["bash", "-c", "echo hello world"]\n'
        )

        spec = self._make_spec(tmp_path, typescript=False)
        compose = {
            "services": {
                "backend": {"build": {"context": "./backend"}, "ports": ["8000:8000"]},
            }
        }

        override = generate_compose_override(spec, compose, extension_dir=ext_dir)
        command = override["services"]["backend"]["command"][0]
        assert "exec bash -c 'echo hello world'" in command

    def test_fallback_without_extension_dir(self, tmp_path):
        spec = self._make_spec(tmp_path)
        compose = {
            "services": {
                "backend": {"build": "./backend", "ports": ["8000:8000"]},
            }
        }
        # No extension_dir — uses default fallback commands
        override = generate_compose_override(spec, compose)
        services = override["services"]
        assert "exec uvicorn" in services["backend"]["command"][0]

    def test_python_only(self, tmp_path):
        spec = self._make_spec(tmp_path, typescript=False)
        compose = {
            "services": {
                "frontend": {"build": "./frontend", "ports": ["3000:3000"]},
                "backend": {"build": "./backend", "ports": ["8000:8000"]},
            }
        }
        override = generate_compose_override(spec, compose)
        services = override["services"]
        assert (
            'export PYTHONPATH="/sdk$${PYTHONPATH:+:$$PYTHONPATH}"'
            in services["backend"]["command"][0]
        )
        assert "frontend" not in services

    def test_typescript_only(self, tmp_path):
        spec = self._make_spec(tmp_path, python=False)
        compose = {
            "services": {
                "frontend": {"build": "./frontend", "ports": ["3000:3000"]},
                "backend": {"build": "./backend", "ports": ["8000:8000"]},
            }
        }

        override = generate_compose_override(spec, compose)
        services = override["services"]
        assert "npm pack" in services["frontend"]["command"][0]
        assert "backend" not in services

    def test_python_override_prefers_local_sdk_via_pythonpath(self, tmp_path):
        spec = self._make_spec(tmp_path, typescript=False)
        compose = {
            "services": {
                "backend": {"build": "./backend", "ports": ["8000:8000"]},
            }
        }

        override = generate_compose_override(spec, compose)
        command = override["services"]["backend"]["command"][0]
        assert 'export PYTHONPATH="/sdk$${PYTHONPATH:+:$$PYTHONPATH}"' in command

    def test_typescript_override_escapes_tarball_for_compose(self, tmp_path):
        spec = self._make_spec(tmp_path, python=False)
        compose = {
            "services": {
                "frontend": {"build": "./frontend", "ports": ["3000:3000"]},
            }
        }

        override = generate_compose_override(spec, compose)
        command = override["services"]["frontend"]["command"][0]
        assert "TARBALL=$$(cd /sdk/kamiwaza-ai-extensions-lib" in command
        assert 'npm install --ignore-scripts "/tmp/$$TARBALL"' in command

    def test_volume_mount_is_readonly(self, tmp_path):
        spec = self._make_spec(tmp_path)
        compose = {"services": {"backend": {"build": ".", "ports": ["8000:8000"]}}}
        override = generate_compose_override(spec, compose)
        volumes = override["services"]["backend"]["volumes"]
        assert any(isinstance(v, dict) and v.get("read_only") for v in volumes)

    def test_skips_services_without_build(self, tmp_path):
        spec = self._make_spec(tmp_path)
        compose = {
            "services": {
                "backend": {"build": "./backend", "ports": ["8000:8000"]},
                "redis": {"image": "redis:7"},
            }
        }
        override = generate_compose_override(spec, compose)
        assert "redis" not in override["services"]
        assert "backend" in override["services"]

    def test_empty_services(self, tmp_path):
        spec = self._make_spec(tmp_path)
        override = generate_compose_override(spec, {"services": {}})
        assert override == {"services": {}}

    def test_static_nginx_service_gets_no_override(self, tmp_path):
        ext_dir = tmp_path / "ext"
        ext_dir.mkdir()
        web = ext_dir / "web"
        web.mkdir()
        (web / "Dockerfile").write_text(
            "FROM nginx:alpine\nCOPY index.html /usr/share/nginx/html/index.html\n"
        )

        spec = self._make_spec(tmp_path)
        compose = {
            "services": {
                "mihari-demo": {"build": {"context": "./web"}, "ports": ["8080:80"]},
            }
        }

        override = generate_compose_override(spec, compose, extension_dir=ext_dir)
        assert override == {"services": {}}


# ------------------------------------------------------------------
# generate_build_overrides (remote deploy)
# ------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateBuildOverrides:
    def _make_spec(self, tmp_path, **kwargs):
        return SdkOverrideSpec(sdk_repo=tmp_path, **kwargs)

    def test_both_libs(self, tmp_path):
        spec = self._make_spec(tmp_path)
        compose = {
            "services": {
                "frontend": {"build": "./frontend", "ports": ["3000:3000"]},
                "backend": {"build": "./backend", "ports": ["8000:8000"]},
            }
        }
        overrides = generate_build_overrides(spec, compose)
        assert len(overrides) == 2
        names = {o.service_name for o in overrides}
        assert names == {"frontend", "backend"}

        backend = [o for o in overrides if o.service_name == "backend"][0]
        assert "kamiwaza_extensions_lib" in backend.overlay_steps
        assert "sdk" in backend.additional_build_contexts

        frontend = [o for o in overrides if o.service_name == "frontend"][0]
        assert "npm pack" in frontend.overlay_steps

    def test_python_only(self, tmp_path):
        spec = self._make_spec(tmp_path, typescript=False)
        compose = {
            "services": {
                "frontend": {"build": "./frontend", "ports": ["3000:3000"]},
                "backend": {"build": "./backend", "ports": ["8000:8000"]},
            }
        }
        overrides = generate_build_overrides(spec, compose)
        assert len(overrides) == 1
        assert overrides[0].service_name == "backend"

    def test_skips_services_without_build(self, tmp_path):
        spec = self._make_spec(tmp_path)
        compose = {
            "services": {
                "backend": {"build": "./backend", "ports": ["8000:8000"]},
                "redis": {"image": "redis:7"},
            }
        }
        overrides = generate_build_overrides(spec, compose)
        assert len(overrides) == 1
        assert overrides[0].service_name == "backend"

    def test_overlay_uses_copy_from_sdk(self, tmp_path):
        spec = self._make_spec(tmp_path)
        compose = {"services": {"backend": {"build": ".", "ports": ["8000:8000"]}}}
        overrides = generate_build_overrides(spec, compose)
        assert "COPY --from=sdk" in overrides[0].overlay_steps
        assert "USER root" in overrides[0].overlay_steps

    def test_python_overlay_resolves_site_packages_without_importing_runtime_lib(
        self, tmp_path
    ):
        """ENG-3901 / F-002 round-3: the post-install overlay must resolve
        the site-packages dir via ``sysconfig`` rather than by importing
        ``kamiwaza_extensions_lib``. The pre-install strip removes the lib
        from requirements.txt so the import would crash here. Use
        ``sysconfig.get_paths()['purelib']`` which is always resolvable
        regardless of what's installed."""
        spec = self._make_spec(tmp_path)
        compose = {"services": {"backend": {"build": ".", "ports": ["8000:8000"]}}}
        overlay = generate_build_overrides(spec, compose)[0].overlay_steps
        # Must NOT import the lib (would fail post-strip).
        assert "import kamiwaza_extensions_lib" not in overlay, (
            f"overlay still resolves site-packages by importing the lib — "
            f"this crashes when the pre-install strip removes the pin. "
            f"overlay: {overlay!r}"
        )
        # MUST use sysconfig.get_paths()['purelib'].
        assert "sysconfig" in overlay
        assert "purelib" in overlay

    def test_empty_services(self, tmp_path):
        spec = self._make_spec(tmp_path)
        assert generate_build_overrides(spec, {"services": {}}) == []

    def test_frontend_has_insert_before_build(self, tmp_path):
        spec = self._make_spec(tmp_path)
        compose = {"services": {"frontend": {"build": ".", "ports": ["3000:3000"]}}}
        overrides = generate_build_overrides(spec, compose)
        assert overrides[0].insert_before_build is True

    def test_backend_does_not_insert_before_build(self, tmp_path):
        spec = self._make_spec(tmp_path)
        compose = {"services": {"backend": {"build": ".", "ports": ["8000:8000"]}}}
        overrides = generate_build_overrides(spec, compose)
        assert overrides[0].insert_before_build is False

    def test_static_nginx_service_skips_sdk_override(self, tmp_path):
        ext_dir = tmp_path / "ext"
        ext_dir.mkdir()
        web = ext_dir / "web"
        web.mkdir()
        (web / "Dockerfile").write_text(
            "FROM nginx:alpine\nCOPY index.html /usr/share/nginx/html/index.html\n"
        )

        spec = self._make_spec(tmp_path)
        compose = {
            "services": {
                "mihari-demo": {"build": {"context": "./web"}, "ports": ["8080:80"]},
            }
        }

        overrides = generate_build_overrides(spec, compose, extension_dir=ext_dir)
        assert overrides == []

    def test_static_nginx_service_skips_sdk_override_with_platform_from(self, tmp_path):
        ext_dir = tmp_path / "ext"
        ext_dir.mkdir()
        web = ext_dir / "web"
        web.mkdir()
        (web / "Dockerfile").write_text(
            "FROM --platform=linux/amd64 nginx:alpine\n"
            "COPY index.html /usr/share/nginx/html/index.html\n"
        )

        spec = self._make_spec(tmp_path)
        compose = {
            "services": {
                "mihari-demo": {"build": {"context": "./web"}, "ports": ["8080:80"]},
            }
        }

        overrides = generate_build_overrides(spec, compose, extension_dir=ext_dir)
        assert overrides == []

    def test_static_nginx_service_skips_sdk_override_with_final_alias(self, tmp_path):
        ext_dir = tmp_path / "ext"
        ext_dir.mkdir()
        web = ext_dir / "web"
        web.mkdir()
        (web / "Dockerfile").write_text(
            "FROM nginx:alpine AS runtime\n"
            "FROM runtime\n"
            "COPY index.html /usr/share/nginx/html/index.html\n"
        )

        spec = self._make_spec(tmp_path)
        compose = {
            "services": {
                "web": {"build": {"context": "./web"}, "ports": ["8080:80"]},
            }
        }

        overrides = generate_build_overrides(spec, compose, extension_dir=ext_dir)
        assert overrides == []

    def test_multistage_node_to_nginx_frontend_gets_build_override(self, tmp_path):
        ext_dir = tmp_path / "ext"
        ext_dir.mkdir()
        web = ext_dir / "web"
        web.mkdir()
        (web / "Dockerfile").write_text(
            "FROM node:20 AS build\n"
            "WORKDIR /app\n"
            "COPY package.json package-lock.json ./\n"
            "RUN npm ci\n"
            "COPY . .\n"
            "RUN npm run build\n"
            "FROM nginx:alpine\n"
            "COPY --from=build /app/dist /usr/share/nginx/html\n"
        )

        spec = self._make_spec(tmp_path, python=False)
        compose = {
            "services": {
                "frontend": {"build": {"context": "./web"}, "ports": ["8080:80"]},
            }
        }

        overrides = generate_build_overrides(spec, compose, extension_dir=ext_dir)

        assert len(overrides) == 1
        assert overrides[0].service_name == "frontend"
        assert "npm pack" in overrides[0].overlay_steps
        assert overrides[0].insert_before_build is True


# ------------------------------------------------------------------
# apply_build_overlay
# ------------------------------------------------------------------


@pytest.mark.unit
class TestApplyBuildOverlay:
    def test_appends_when_no_build_line(self):
        dockerfile = 'FROM node:20\nCOPY . .\nENTRYPOINT ["node", "start.mjs"]\n'
        overlay = BuildOverride(
            service_name="frontend",
            overlay_steps="# overlay\nRUN echo hello\n",
            additional_build_contexts={},
            insert_before_build=True,
        )
        result = apply_build_overlay(dockerfile, overlay)
        assert result.endswith("RUN echo hello\n")

    def test_inserts_before_npm_run_build(self):
        dockerfile = 'FROM node:20\nCOPY . .\nRUN npm run build\nCMD ["npm", "start"]\n'
        overlay = BuildOverride(
            service_name="frontend",
            overlay_steps="# SDK override\nRUN echo injected\n",
            additional_build_contexts={},
            insert_before_build=True,
        )
        result = apply_build_overlay(dockerfile, overlay)
        lines = result.splitlines()
        build_idx = next(i for i, line in enumerate(lines) if "npm run build" in line)
        inject_idx = next(i for i, line in enumerate(lines) if "echo injected" in line)
        assert inject_idx < build_idx

    def test_inserts_before_next_build(self):
        dockerfile = "FROM node:20\nCOPY . .\nRUN next build\n"
        overlay = BuildOverride(
            service_name="frontend",
            overlay_steps="# SDK\n",
            additional_build_contexts={},
            insert_before_build=True,
        )
        result = apply_build_overlay(dockerfile, overlay)
        assert result.index("# SDK") < result.index("next build")

    def test_appends_when_insert_before_build_false(self):
        dockerfile = "FROM python:3.10\nRUN pip install .\n"
        overlay = BuildOverride(
            service_name="backend",
            overlay_steps="# overlay\n",
            additional_build_contexts={},
            insert_before_build=False,
        )
        result = apply_build_overlay(dockerfile, overlay)
        assert result.endswith("# overlay\n")

    def test_restores_detected_user_when_appending_overlay(self):
        dockerfile = "FROM python:3.10\nUSER appuser\n"
        overlay = BuildOverride(
            service_name="backend",
            overlay_steps="# overlay\nUSER root\nRUN echo injected\n{restore_user_block}",
            additional_build_contexts={},
            insert_before_build=False,
        )

        result = apply_build_overlay(dockerfile, overlay)
        assert "RUN echo injected" in result
        assert "USER appuser" in result

    def test_keeps_root_before_build_when_no_user_declared_yet(self):
        dockerfile = "FROM node:20\nCOPY . .\nRUN npm run build\nUSER 1001\n"
        overlay = BuildOverride(
            service_name="frontend",
            overlay_steps="# overlay\nUSER root\nRUN echo injected\n{restore_user_block}",
            additional_build_contexts={},
            insert_before_build=True,
        )

        result = apply_build_overlay(dockerfile, overlay)
        prefix, _build_line = result.split("RUN npm run build", maxsplit=1)
        assert "RUN echo injected" in prefix
        assert "USER 1001" not in prefix


# ------------------------------------------------------------------
# pre_install_steps — strip kamiwaza-extensions-lib before pip install
# (PR #89 dry-run finding F-002)
# ------------------------------------------------------------------


@pytest.mark.unit
class TestPreInstallStripOverlay:
    """The Python overlay must strip the runtime-lib pin from
    requirements.txt *before* the scaffold's pip-install step runs, so the
    docker build doesn't fail when the pinned version isn't on PyPI yet.
    The post-install overlay then drops local source into site-packages.

    Regression: previously the overlay only appended; pip install would
    fail before the appended COPY/cp could replace site-packages."""

    SCAFFOLD_DOCKERFILE = (
        "FROM python:3.10-slim\n"
        "RUN groupadd -r -g 1001 appuser && useradd -r -u 1001 -g appuser -d /app appuser\n"
        "WORKDIR /app\n"
        "COPY requirements.txt .\n"
        "RUN pip install --no-cache-dir -r requirements.txt\n"
        "COPY . .\n"
        "USER 1001\n"
        'CMD ["uvicorn", "app.main:app"]\n'
    )

    def test_backend_override_carries_pre_install_steps(self, tmp_path):
        """``generate_build_overrides`` must wire the strip step onto every
        Python backend override so ``--sdk-repo`` works against a scaffold
        whose runtime-lib pin isn't installable from PyPI."""
        from kamiwaza_extensions.sdk_override import _PYTHON_PRE_INSTALL_STRIP

        spec = SdkOverrideSpec(sdk_repo=tmp_path)
        compose = {
            "services": {"backend": {"build": "./backend", "ports": ["8000:8000"]}}
        }
        overrides = generate_build_overrides(spec, compose)
        assert len(overrides) == 1
        assert overrides[0].pre_install_steps == _PYTHON_PRE_INSTALL_STRIP

    def test_frontend_override_carries_ts_pre_install_steps(self, tmp_path):
        """``generate_build_overrides`` (cluster deploy path) must also wire
        the TS strip step onto every frontend override. Round-2 of F-002:
        the cluster-deploy build hits the same npm ETARGET as ``dev local``
        when ``@kamiwaza-ai/extensions-lib`` isn't published, but the dev
        local fix only landed in ``generate_local_build_dockerfile_patches``
        — leaving ``kz-ext dev`` broken."""
        from kamiwaza_extensions.sdk_override import _TS_PRE_INSTALL_STRIP

        spec = SdkOverrideSpec(sdk_repo=tmp_path)
        compose = {
            "services": {"frontend": {"build": "./frontend", "ports": ["3000:3000"]}}
        }
        overrides = generate_build_overrides(spec, compose)
        assert len(overrides) == 1
        assert overrides[0].pre_install_steps == _TS_PRE_INSTALL_STRIP

    def test_apply_build_overlay_inserts_ts_strip_before_npm_install(self):
        """The overlay applier must locate ``RUN npm install`` (not just
        ``pip install``) when the pre_install_steps target the frontend."""
        ts_dockerfile = (
            "FROM node:20-alpine\nWORKDIR /app\n"
            "COPY package.json package-lock.json* ./\n"
            "RUN npm install\nCOPY . .\n"
            "RUN npm run build\n"
            'CMD ["node", "/app/start.mjs"]\n'
        )
        overlay = BuildOverride(
            service_name="frontend",
            overlay_steps="# post-install\nRUN echo post\n",
            additional_build_contexts={"sdk": "/tmp/sdk"},
            insert_before_build=True,
            pre_install_steps=(
                "# ts-strip\nUSER root\nRUN node -e 'strip'\n{restore_user_block}"
            ),
        )
        result = apply_build_overlay(ts_dockerfile, overlay)
        assert "# ts-strip" in result
        assert result.index("# ts-strip") < result.index("RUN npm install")

    def test_strip_step_inserted_before_pip_install(self):
        overlay = BuildOverride(
            service_name="backend",
            overlay_steps="# post-install\nRUN echo post\n",
            additional_build_contexts={"sdk": "/tmp/sdk"},
            pre_install_steps=(
                "# strip\nUSER root\nRUN sed -i '/x/d' requirements.txt\n"
                "{restore_user_block}"
            ),
        )
        result = apply_build_overlay(self.SCAFFOLD_DOCKERFILE, overlay)
        # Strip must appear in the output and BEFORE the pip install line.
        assert "# strip" in result
        assert result.index("# strip") < result.index("pip install")
        # Post-install overlay still appended.
        assert result.index("pip install") < result.index("# post-install")

    def test_strip_step_uses_root_then_restores_active_user(self):
        """The strip overlay must drop into root and restore the user that
        was active before it. In the scaffold's Dockerfile the pip-install
        runs as root (no USER set yet), so no restore is needed."""
        overlay = BuildOverride(
            service_name="backend",
            overlay_steps="",
            additional_build_contexts={"sdk": "/tmp/sdk"},
            pre_install_steps=("USER root\nRUN echo strip\n{restore_user_block}"),
        )
        result = apply_build_overlay(self.SCAFFOLD_DOCKERFILE, overlay)
        # USER root is set explicitly in the strip block; nothing to restore
        # because the scaffold has no USER directive before pip install.
        strip_section, _, _ = result.partition("RUN pip install")
        assert "USER root" in strip_section
        # The trailing USER 1001 in the original Dockerfile is untouched.
        assert result.rstrip().endswith('CMD ["uvicorn", "app.main:app"]')

    def test_strip_step_restores_non_root_user_when_pip_install_runs_as_appuser(self):
        """A Dockerfile that switches to a non-root user before pip install
        must have that user restored after the strip block — otherwise the
        pip install would unexpectedly run as root."""
        df = (
            "FROM python:3.10-slim\nRUN useradd -m appuser\nWORKDIR /app\n"
            "COPY requirements.txt .\nUSER appuser\n"
            "RUN pip install -r requirements.txt\n"
        )
        overlay = BuildOverride(
            service_name="backend",
            overlay_steps="",
            additional_build_contexts={"sdk": "/tmp/sdk"},
            pre_install_steps=("USER root\nRUN echo strip\n{restore_user_block}"),
        )
        result = apply_build_overlay(df, overlay)
        # The strip's USER root block must be followed by USER appuser
        # before the pip install line.
        strip_section, _, after = result.partition("RUN pip install")
        assert "USER root" in strip_section
        assert "RUN echo strip" in strip_section
        assert "USER appuser" in strip_section
        # The order is: USER appuser (original) -> USER root + strip ->
        # USER appuser (restore) -> RUN pip install.
        last_user_before_install = strip_section.rstrip().splitlines()[-1]
        assert last_user_before_install.strip() == "USER appuser"

    def test_strip_step_is_a_no_op_when_no_pip_install_line(self):
        """Custom Dockerfile (e.g. poetry-based) without
        ``RUN ... pip install -r requirements.txt`` should be left alone by
        the strip step — the post-install overlay still appends as before."""
        df = "FROM python:3.10-slim\nRUN poetry install\n"
        overlay = BuildOverride(
            service_name="backend",
            overlay_steps="# post-install\nRUN echo post\n",
            additional_build_contexts={"sdk": "/tmp/sdk"},
            pre_install_steps=("USER root\nRUN echo strip\n{restore_user_block}"),
        )
        result = apply_build_overlay(df, overlay)
        assert "RUN echo strip" not in result
        assert result.endswith("# post-install\nRUN echo post\n")

    def test_generate_local_build_dockerfile_patches_handles_backend_and_frontend(
        self, tmp_path
    ):
        """``dev local`` failure path on freshly-scaffolded extensions:
        BOTH the Python backend (pip install) and the TS frontend
        (npm install) blow up before the runtime overlay can rescue
        them when their respective runtime-lib pins aren't published.
        ``generate_local_build_dockerfile_patches`` must produce a
        patched Dockerfile per service so the build phase succeeds."""
        from kamiwaza_extensions.sdk_override import (
            generate_local_build_dockerfile_patches,
        )

        ext = tmp_path / "ext"
        ext.mkdir()
        backend = ext / "backend"
        backend.mkdir()
        (backend / "Dockerfile").write_text(
            "FROM python:3.10-slim\nWORKDIR /app\nCOPY requirements.txt .\n"
            "RUN pip install --no-cache-dir -r requirements.txt\nCOPY . .\n"
        )
        (backend / "requirements.txt").write_text(
            "fastapi>=0.100.0\nkamiwaza-extensions-lib>=0.4,<0.5\n"
        )

        frontend = ext / "frontend"
        frontend.mkdir()
        (frontend / "Dockerfile").write_text(
            "FROM node:20-alpine\nWORKDIR /app\nCOPY package.json .\n"
            "RUN npm install\nCOPY . .\n"
        )
        (frontend / "package.json").write_text(
            '{"dependencies": {"@kamiwaza-ai/extensions-lib": ">=0.4 <0.5"}}'
        )

        spec = SdkOverrideSpec(sdk_repo=tmp_path / "sdk")
        compose = {
            "services": {
                "backend": {"build": "./backend", "ports": ["8000:8000"]},
                "frontend": {"build": "./frontend", "ports": ["3000:3000"]},
            }
        }
        patches = generate_local_build_dockerfile_patches(spec, compose, ext)

        assert set(patches.keys()) == {"backend", "frontend"}
        assert "kamiwaza-extensions-lib" in patches["backend"]
        # Backend patch: sed strip is inserted before pip install.
        assert patches["backend"].index("sed -i") < patches["backend"].index(
            "pip install"
        )
        # Frontend patch: node-based JSON strip is inserted before npm install.
        assert "@kamiwaza-ai/extensions-lib" in patches["frontend"]
        assert patches["frontend"].index("node -e") < patches["frontend"].index(
            "npm install"
        )

    def test_is_typescript_dist_stale_detects_src_newer_than_dist(self, tmp_path):
        """``--sdk-repo`` must auto-rebuild when src/ has changes that
        haven't reached dist/. Without this, a ``git pull`` that adds a
        new subpath export silently produces a "Module not found" at the
        consumer (PR #87 / F-006: ``dist/local-dev-auth/`` declared in
        package.json but never built before merge)."""
        import time

        from kamiwaza_extensions.sdk_override import is_typescript_dist_stale

        ts_lib = tmp_path / "kamiwaza-ai-extensions-lib"
        ts_lib.mkdir()
        (ts_lib / "dist").mkdir()
        (ts_lib / "src").mkdir()
        (ts_lib / "dist" / "index.js").write_text("//")
        time.sleep(0.05)
        (ts_lib / "src" / "index.ts").write_text("// newer")

        spec = SdkOverrideSpec(sdk_repo=tmp_path)
        assert is_typescript_dist_stale(spec) is True

    def test_is_typescript_dist_stale_returns_false_when_dist_is_fresh(self, tmp_path):
        import time

        from kamiwaza_extensions.sdk_override import is_typescript_dist_stale

        ts_lib = tmp_path / "kamiwaza-ai-extensions-lib"
        ts_lib.mkdir()
        (ts_lib / "src").mkdir()
        (ts_lib / "dist").mkdir()
        (ts_lib / "src" / "index.ts").write_text("//")
        time.sleep(0.05)
        (ts_lib / "dist" / "index.js").write_text("// newer")

        spec = SdkOverrideSpec(sdk_repo=tmp_path)
        assert is_typescript_dist_stale(spec) is False

    def test_is_typescript_dist_stale_returns_false_when_dist_is_missing(
        self, tmp_path
    ):
        """Missing dist/ is a different signal — handled separately by the
        caller. ``is_typescript_dist_stale`` only answers the comparison
        question when both src/ and dist/ exist."""
        from kamiwaza_extensions.sdk_override import is_typescript_dist_stale

        ts_lib = tmp_path / "kamiwaza-ai-extensions-lib"
        ts_lib.mkdir()
        (ts_lib / "src").mkdir()
        (ts_lib / "src" / "index.ts").write_text("//")

        spec = SdkOverrideSpec(sdk_repo=tmp_path)
        assert is_typescript_dist_stale(spec) is False

    def test_generate_local_build_dockerfile_patches_skips_when_no_install_line(
        self, tmp_path
    ):
        """Custom Dockerfile (e.g. poetry-based) without a recognizable
        install line is left out of the patch dict — the user's Dockerfile
        owns its own runtime-lib install."""
        from kamiwaza_extensions.sdk_override import (
            generate_local_build_dockerfile_patches,
        )

        ext = tmp_path / "ext"
        ext.mkdir()
        backend = ext / "backend"
        backend.mkdir()
        (backend / "Dockerfile").write_text(
            "FROM python:3.10-slim\nRUN poetry install\n"
        )

        spec = SdkOverrideSpec(sdk_repo=tmp_path / "sdk")
        compose = {
            "services": {"backend": {"build": "./backend", "ports": ["8000:8000"]}}
        }
        assert generate_local_build_dockerfile_patches(spec, compose, ext) == {}

    def test_strip_regex_matches_real_pin_forms_and_skips_prefix_aliases(self):
        """Sanity-check the embedded sed pattern against realistic
        requirements.txt content: every pin form for the runtime lib should
        be removed; siblings whose name *starts with* the lib name (e.g.
        ``kamiwaza-extensions-lib-extras``) and unrelated lines stay."""
        import re

        # Mirror of the sed pattern used in _PYTHON_PRE_INSTALL_STRIP, but
        # rewritten as a Python regex (POSIX [[:space:]] -> \s).
        pattern = re.compile(
            r"^\s*kamiwaza-extensions-lib($|[^A-Za-z0-9_-])", re.MULTILINE
        )
        requirements = (
            "fastapi>=0.100.0\n"
            "kamiwaza-extensions-lib>=0.4,<0.5\n"
            "kamiwaza-extensions-lib==0.1.0\n"
            "kamiwaza-extensions-lib\n"
            "  kamiwaza-extensions-lib>=0.4\n"
            "kamiwaza-extensions-lib[extras]>=0.4\n"
            "kamiwaza-extensions-lib-extras>=0.1\n"
            "# kamiwaza-extensions-lib>=0.4 (commented)\n"
        )
        kept = [line for line in requirements.splitlines() if not pattern.match(line)]
        assert "fastapi>=0.100.0" in kept
        assert (
            "kamiwaza-extensions-lib-extras>=0.1" in kept
        ), "prefix-alias must NOT be stripped"
        assert "# kamiwaza-extensions-lib>=0.4 (commented)" in kept
        # Every form of the actual runtime-lib pin is gone.
        assert not any("kamiwaza-extensions-lib>=0.4,<0.5" in k for k in kept)
        assert not any("kamiwaza-extensions-lib==0.1.0" in k for k in kept)
        assert not any(k.strip() == "kamiwaza-extensions-lib" for k in kept)
        assert not any("kamiwaza-extensions-lib[extras]" in k for k in kept)


# ------------------------------------------------------------------
# Pre-install strip steps (PR #91 round-2 review hardening): file-exists
# guard fails open when the canonical filename is missing, and the TS
# strip covers every npm dep-map key plus overrides/resolutions.
# ------------------------------------------------------------------


def _extract_run_command(template: str) -> str:
    """Pull the ``RUN ...`` body out of one of the strip templates.

    The templates contain a ``USER root\\n`` line, then a ``RUN <body>\\n``,
    then a trailing ``{restore_user_block}`` placeholder. This helper
    returns just ``<body>`` so a test can hand it to ``bash -c``.
    """
    lines = template.splitlines()
    run_idx = next(i for i, ln in enumerate(lines) if ln.startswith("RUN "))
    return lines[run_idx][len("RUN ") :]


@pytest.mark.unit
class TestPreInstallStripExecution:
    """Behavioral coverage for the file-exists guards and the expanded
    TS strip key set added in the round-2 review of PR #91."""

    def test_python_strip_is_no_op_when_requirements_txt_missing(self, tmp_path):
        """ENG-3901 / round-2: ``[ -f requirements.txt ]`` guard fails open
        on non-canonical Dockerfile layouts."""
        import shutil
        import subprocess

        from kamiwaza_extensions.sdk_override import _PYTHON_PRE_INSTALL_STRIP

        if not shutil.which("bash"):
            pytest.skip("bash not available")

        cmd = _extract_run_command(_PYTHON_PRE_INSTALL_STRIP)
        result = subprocess.run(
            ["bash", "-c", cmd],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        # No file created, no stderr noise.
        assert list(tmp_path.iterdir()) == []
        assert result.stderr == ""

    def test_python_strip_removes_pin_when_requirements_txt_present(self, tmp_path):
        import shutil
        import subprocess

        from kamiwaza_extensions.sdk_override import _PYTHON_PRE_INSTALL_STRIP

        if not shutil.which("bash"):
            pytest.skip("bash not available")
        # GNU sed is required for ``-i`` without a backup arg + POSIX
        # character classes. macOS ships BSD sed, so install gnu-sed
        # locally would be needed; the Dockerfile only runs in Linux
        # build contexts so we gate to GNU sed only.
        sed_path = shutil.which("sed")
        if not sed_path:
            pytest.skip("sed not available")
        sed_help = subprocess.run(
            [sed_path, "--version"], capture_output=True, text=True
        )
        if "GNU sed" not in sed_help.stdout:
            pytest.skip("strip requires GNU sed; this host has BSD sed")

        (tmp_path / "requirements.txt").write_text(
            "fastapi>=0.100.0\n"
            "kamiwaza-extensions-lib>=0.4,<0.5\n"
            "kamiwaza-extensions-lib-extras>=0.1\n"
        )
        cmd = _extract_run_command(_PYTHON_PRE_INSTALL_STRIP)
        result = subprocess.run(
            ["bash", "-c", cmd],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        kept = (tmp_path / "requirements.txt").read_text()
        assert "fastapi>=0.100.0" in kept
        assert "kamiwaza-extensions-lib-extras>=0.1" in kept
        assert "kamiwaza-extensions-lib>=0.4,<0.5" not in kept

    def test_ts_strip_is_no_op_when_package_json_missing(self, tmp_path):
        """ENG-3901 / round-2: ``[ -f package.json ]`` guard fails open
        on non-canonical Dockerfile layouts."""
        import shutil
        import subprocess

        from kamiwaza_extensions.sdk_override import _TS_PRE_INSTALL_STRIP

        if not shutil.which("bash") or not shutil.which("node"):
            pytest.skip("bash + node required")

        cmd = _extract_run_command(_TS_PRE_INSTALL_STRIP)
        result = subprocess.run(
            ["bash", "-c", cmd],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert list(tmp_path.iterdir()) == []
        assert result.stderr == ""

    def test_ts_strip_removes_lib_from_every_dep_map_key(self, tmp_path):
        """ENG-3901 / round-2: ``optionalDependencies``,
        ``bundleDependencies`` / ``bundledDependencies``, ``overrides``,
        and ``resolutions`` are all covered alongside the three
        documented dep maps. Both object and array forms handled."""
        import json as _json
        import shutil
        import subprocess

        from kamiwaza_extensions.sdk_override import _TS_PRE_INSTALL_STRIP

        if not shutil.which("bash") or not shutil.which("node"):
            pytest.skip("bash + node required")

        package = {
            "name": "stripper-fixture",
            "version": "0.0.0",
            "dependencies": {
                "@kamiwaza-ai/extensions-lib": "^0.4.0",
                "react": "^18.0.0",
            },
            "devDependencies": {"@kamiwaza-ai/extensions-lib": "^0.4.0"},
            "peerDependencies": {"@kamiwaza-ai/extensions-lib": "^0.4.0"},
            "optionalDependencies": {"@kamiwaza-ai/extensions-lib": "^0.4.0"},
            "bundleDependencies": ["@kamiwaza-ai/extensions-lib", "react"],
            "bundledDependencies": ["@kamiwaza-ai/extensions-lib"],
            "overrides": {"@kamiwaza-ai/extensions-lib": "0.4.0"},
            "resolutions": {"@kamiwaza-ai/extensions-lib": "0.4.0"},
        }
        (tmp_path / "package.json").write_text(_json.dumps(package, indent=2))

        cmd = _extract_run_command(_TS_PRE_INSTALL_STRIP)
        result = subprocess.run(
            ["bash", "-c", cmd],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        out = _json.loads((tmp_path / "package.json").read_text())
        for k in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
            "overrides",
            "resolutions",
        ):
            assert "@kamiwaza-ai/extensions-lib" not in out[k], (
                f"{k!r} still contains the lib"
            )
        assert "@kamiwaza-ai/extensions-lib" not in out["bundleDependencies"]
        assert "@kamiwaza-ai/extensions-lib" not in out["bundledDependencies"]
        # Unrelated entries survive.
        assert out["dependencies"]["react"] == "^18.0.0"
        assert "react" in out["bundleDependencies"]


# ------------------------------------------------------------------
# validate_sdk_override — path safety
# ------------------------------------------------------------------


@pytest.mark.unit
class TestValidatePathSafety:
    def test_path_with_equals_rejected(self, tmp_path):
        bad_path = tmp_path / "foo=bar"
        bad_path.mkdir()
        spec = SdkOverrideSpec(sdk_repo=bad_path)
        result = validate_sdk_override(spec)
        assert not result.ok
        assert any("=" in e for e in result.errors)


# ------------------------------------------------------------------
# Doctor SDK checks
# ------------------------------------------------------------------


@pytest.mark.unit
class TestDoctorSdkChecks:
    def test_no_config_returns_empty(self, tmp_path, monkeypatch):
        from kamiwaza_extensions.doctor import DoctorChecker

        monkeypatch.chdir(tmp_path)
        checker = DoctorChecker()
        results = checker._check_sdk_override()
        assert results == []

    def test_valid_config_returns_checks(self, tmp_path, monkeypatch):
        import time

        from kamiwaza_extensions.doctor import DoctorChecker

        # Set up SDK repo structure
        (tmp_path / "kamiwaza_extensions_lib").mkdir()
        ts_lib = tmp_path / "kamiwaza-ai-extensions-lib"
        ts_lib.mkdir()
        (ts_lib / "src").mkdir()
        (ts_lib / "src" / "index.ts").write_text("//")
        time.sleep(0.05)
        (ts_lib / "dist").mkdir()
        (ts_lib / "dist" / "index.js").write_text("//")

        # Set up extension with .kz-ext/local.yaml
        ext_dir = tmp_path / "my-ext"
        ext_dir.mkdir()
        (ext_dir / "kamiwaza.json").write_text('{"name": "test", "version": "0.1.0"}')
        (ext_dir / ".kz-ext").mkdir()
        import yaml

        (ext_dir / ".kz-ext" / "local.yaml").write_text(
            yaml.dump({"sdk_repo": str(tmp_path)})
        )
        # Template contract files
        backend = ext_dir / "backend"
        backend.mkdir()
        (backend / "requirements.txt").write_text("kamiwaza-extensions-lib>=0.1.0\n")
        frontend = ext_dir / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text(
            '{"dependencies": {"@kamiwaza-ai/extensions-lib": "^0.2.0"}}'
        )

        monkeypatch.chdir(ext_dir)
        checker = DoctorChecker()
        results = checker._check_sdk_override()

        names = [r.name for r in results]
        assert "SDK override config" in names
        assert "SDK repo exists" in names
        assert "SDK Python lib" in names
        assert "SDK TypeScript lib" in names
        assert "SDK TypeScript dist/" in names
        assert "SDK override contract" in names

        assert all(r.status == "pass" for r in results)
