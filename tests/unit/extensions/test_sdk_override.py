"""Tests for SDK override config, validation, and compose generation."""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from kamiwaza_extensions.sdk_override import (
    BuildOverride,
    SdkOverrideSpec,
    ValidationResult,
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
        assert spec.typescript_dist_path == tmp_path / "kamiwaza-ai-extensions-lib" / "dist"

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
        (config_dir / "local.yaml").write_text(
            yaml.dump({"sdk_repo": "/other/path"})
        )
        spec = resolve_sdk_override(str(tmp_path), extension_path=tmp_path)
        assert spec is not None
        assert spec.sdk_repo == tmp_path  # CLI wins

    def test_config_file(self, tmp_path):
        config_dir = tmp_path / ".kz-ext"
        config_dir.mkdir()
        (config_dir / "local.yaml").write_text(
            yaml.dump({"sdk_repo": str(tmp_path)})
        )
        spec = resolve_sdk_override(None, extension_path=tmp_path)
        assert spec is not None
        assert spec.sdk_repo == tmp_path

    def test_config_file_selective_libs(self, tmp_path):
        config_dir = tmp_path / ".kz-ext"
        config_dir.mkdir()
        (config_dir / "local.yaml").write_text(
            yaml.dump({
                "sdk_repo": str(tmp_path),
                "runtime_libs": {"python": "local", "typescript": "published"},
                "build_typescript": True,
            })
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
        (config_dir / "local.yaml").write_text(
            yaml.dump({"build_typescript": True})
        )
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

    def test_both_libs(self, tmp_path):
        spec = self._make_spec(tmp_path)
        compose = {
            "services": {
                "frontend": {"build": "./frontend", "ports": ["3000:3000"]},
                "backend": {"build": "./backend", "ports": ["8000:8000"]},
            }
        }
        override = generate_compose_override(spec, compose)
        services = override["services"]

        # Backend uses exec "$@" pattern — entrypoint set, no command
        assert "kamiwaza_extensions_lib" in services["backend"]["entrypoint"][2]
        assert 'exec "$@"' in services["backend"]["entrypoint"][2]
        assert "command" not in services["backend"]

        # Frontend gets npm pack + exec npm start
        assert "npm pack" in services["frontend"]["command"][0]

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

        assert "kamiwaza_extensions_lib" in services["backend"]["entrypoint"][2]
        # Frontend not in override (no TS override, no build-less service)
        assert "frontend" not in services or "command" not in services.get("frontend", {})

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
        assert "backend" not in services or "entrypoint" not in services.get("backend", {})

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


# ------------------------------------------------------------------
# apply_build_overlay
# ------------------------------------------------------------------


@pytest.mark.unit
class TestApplyBuildOverlay:
    def test_appends_when_no_build_line(self):
        dockerfile = "FROM node:20\nCOPY . .\nENTRYPOINT [\"node\", \"start.mjs\"]\n"
        overlay = BuildOverride(
            service_name="frontend",
            overlay_steps="# overlay\nRUN echo hello\n",
            additional_build_contexts={},
            insert_before_build=True,
        )
        result = apply_build_overlay(dockerfile, overlay)
        assert result.endswith("RUN echo hello\n")

    def test_inserts_before_npm_run_build(self):
        dockerfile = "FROM node:20\nCOPY . .\nRUN npm run build\nCMD [\"npm\", \"start\"]\n"
        overlay = BuildOverride(
            service_name="frontend",
            overlay_steps="# SDK override\nRUN echo injected\n",
            additional_build_contexts={},
            insert_before_build=True,
        )
        result = apply_build_overlay(dockerfile, overlay)
        lines = result.splitlines()
        build_idx = next(i for i, l in enumerate(lines) if "npm run build" in l)
        inject_idx = next(i for i, l in enumerate(lines) if "echo injected" in l)
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
        from kamiwaza_extensions.doctor import DoctorChecker
        import time

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
