"""Unit tests for kz-ext validate command."""

import json

import pytest
import yaml

pytestmark = pytest.mark.unit


def _valid_metadata() -> dict:
    return {
        "name": "demo-app",
        "version": "0.1.0",
        "source_type": "kamiwaza",
        "visibility": "private",
        "description": "A demo extension",
        "risk_tier": 1,
        "verified": False,
    }


def _write_extension_files(tmp_path, compose, dockerfile_text, extra_files=None):
    (tmp_path / "kamiwaza.json").write_text(json.dumps(_valid_metadata()))
    (tmp_path / "docker-compose.yml").write_text(yaml.dump(compose))
    (tmp_path / "Dockerfile").write_text(dockerfile_text)
    for rel_path, content in (extra_files or {}).items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


class TestRunValidate:
    def test_missing_manifest_exits_with_json_error(self, tmp_path, capsys):
        import typer

        from kamiwaza_extensions.commands.validate import run_validate

        with pytest.raises(typer.Exit):
            run_validate(path=str(tmp_path), json_output=True)

        output = json.loads(capsys.readouterr().out)
        assert output["passed"] is False
        assert any("No kamiwaza.json found" in err for err in output["errors"])

    def test_invalid_manifest_emits_failed_json(self, tmp_path, capsys):
        """Codex iter-2 finding: ``kz-ext validate`` is the canonical
        way to discover that a manifest is broken — the validator
        should surface its specific JSON-decode error from
        ``MetadataValidator``, NOT bail with the detector's generic
        "cannot read" message before validation runs."""
        import typer

        from kamiwaza_extensions.commands.validate import run_validate

        (tmp_path / "kamiwaza.json").write_text("{bad json")

        with pytest.raises(typer.Exit):
            run_validate(path=str(tmp_path), json_output=True)

        output = json.loads(capsys.readouterr().out)
        assert output["passed"] is False
        assert output["errors"]
        # Joined error text should reflect a JSON-parse failure (the
        # MetadataValidator's specific error class), not a "cannot
        # read kamiwaza.json" generic detector wrapping.
        joined = " ".join(output["errors"]).lower()
        assert "json" in joined or "parse" in joined or "decode" in joined, (
            f"Expected a JSON-parse error from MetadataValidator, got: {output['errors']}"
        )

    def test_warnings_only_do_not_fail_validation(self, tmp_path, capsys):
        from kamiwaza_extensions.commands.validate import run_validate

        compose = {
            "services": {
                "web": {
                    "image": "nginxinc/nginx-unprivileged:stable-alpine",
                    "ports": ["8080:8080"],
                    "deploy": {"resources": {"limits": {"cpus": "1.0", "memory": "1G"}}},
                },
            },
        }
        (tmp_path / "kamiwaza.json").write_text(json.dumps(_valid_metadata()))
        (tmp_path / "docker-compose.yml").write_text(yaml.dump(compose))

        run_validate(path=str(tmp_path), json_output=True)

        output = json.loads(capsys.readouterr().out)
        assert output["passed"] is True
        assert output["errors"] == []
        assert output["warnings"]

    def test_validate_fails_on_image_only_rootful_nginx_runtime(self, tmp_path, capsys):
        import typer

        from kamiwaza_extensions.commands.validate import run_validate

        compose = {
            "services": {
                "web": {
                    "image": "nginx:alpine",
                    "ports": ["8080:8080"],
                    "deploy": {"resources": {"limits": {"cpus": "1.0", "memory": "1G"}}},
                },
            },
        }
        (tmp_path / "kamiwaza.json").write_text(json.dumps(_valid_metadata()))
        (tmp_path / "docker-compose.yml").write_text(yaml.dump(compose))

        with pytest.raises(typer.Exit):
            run_validate(path=str(tmp_path), json_output=True)

        output = json.loads(capsys.readouterr().out)
        assert output["passed"] is False
        assert any("image-only nginx service" in err for err in output["errors"])

    def test_validate_fails_on_platform_incompatible_runtime(self, tmp_path, capsys):
        import typer

        from kamiwaza_extensions.commands.validate import run_validate

        compose = {
            "services": {
                "web": {
                    "build": ".",
                    "ports": ["8080:80"],
                    "deploy": {"resources": {"limits": {"cpus": "1.0", "memory": "1G"}}},
                },
            },
        }
        _write_extension_files(
            tmp_path,
            compose,
            "FROM nginx:alpine\nCOPY index.html /usr/share/nginx/html/index.html\nEXPOSE 80\n",
        )

        with pytest.raises(typer.Exit):
            run_validate(path=str(tmp_path), json_output=True)

        output = json.loads(capsys.readouterr().out)
        assert output["passed"] is False
        assert any("container port 80 is privileged" in err for err in output["errors"])

    def test_validate_passes_for_unprivileged_static_runtime(self, tmp_path, capsys):
        from kamiwaza_extensions.commands.validate import run_validate

        compose = {
            "services": {
                "web": {
                    "build": ".",
                    "ports": ["8080:8080"],
                    "deploy": {"resources": {"limits": {"cpus": "1.0", "memory": "1G"}}},
                },
            },
        }
        _write_extension_files(
            tmp_path,
            compose,
            (
                "FROM nginxinc/nginx-unprivileged:stable-alpine\n"
                "COPY nginx.conf /etc/nginx/conf.d/default.conf\n"
                "EXPOSE 8080\n"
            ),
            extra_files={
                "nginx.conf": (
                    "server {\n"
                    "    listen 8080;\n"
                    "    client_body_temp_path /tmp/client_temp;\n"
                    "}\n"
                )
            },
        )

        run_validate(path=str(tmp_path), json_output=True)

        output = json.loads(capsys.readouterr().out)
        assert output["passed"] is True
        assert output["errors"] == []


class TestRunValidateMonorepo:
    """validate should descend into monorepo subdirs like kz-ext convert does."""

    def test_finds_extension_under_apps(self, tmp_path, capsys):
        from kamiwaza_extensions.commands.validate import run_validate

        ext = tmp_path / "apps" / "skills-library"
        ext.mkdir(parents=True)
        compose = {
            "services": {
                "web": {
                    "image": "nginxinc/nginx-unprivileged:stable-alpine",
                    "ports": ["8080:8080"],
                    "deploy": {"resources": {"limits": {"cpus": "1.0", "memory": "1G"}}},
                },
            },
        }
        (ext / "kamiwaza.json").write_text(json.dumps(_valid_metadata()))
        (ext / "docker-compose.yml").write_text(yaml.dump(compose))

        run_validate(path=str(tmp_path), json_output=True)

        output = json.loads(capsys.readouterr().out)
        assert output["passed"] is True
        assert output["errors"] == []

    def test_ambiguous_monorepo_exits_with_error(self, tmp_path, capsys):
        import typer

        from kamiwaza_extensions.commands.validate import run_validate

        for sub in ("apps/foo", "tools/bar"):
            d = tmp_path / sub
            d.mkdir(parents=True)
            (d / "kamiwaza.json").write_text(json.dumps(_valid_metadata()))

        with pytest.raises(typer.Exit):
            run_validate(path=str(tmp_path), json_output=True)

        output = json.loads(capsys.readouterr().out)
        assert output["passed"] is False
        joined = " ".join(output["errors"])
        assert "Multiple kamiwaza.json found" in joined


class TestVersionDrift:
    """ENG-4835: surface drift between kamiwaza.json version and sibling files."""

    def _meta(self, version: str, image: str | None = None) -> dict:
        m = _valid_metadata()
        m["version"] = version
        if image is not None:
            m["image"] = image
        return m

    def test_image_tag_drift_warns(self, tmp_path):
        from pathlib import Path
        from kamiwaza_extensions.validators.metadata import MetadataValidator

        (tmp_path / "kamiwaza.json").write_text(
            json.dumps(self._meta("2.1.0", "ghcr.io/x/y:2.0.14"))
        )
        result = MetadataValidator().validate(Path(tmp_path / "kamiwaza.json"))
        assert result.passed
        assert any("image tag" in w and "2.0.14" in w for w in result.warnings)

    def test_compose_image_drift_warns(self, tmp_path):
        from pathlib import Path
        from kamiwaza_extensions.validators.metadata import MetadataValidator

        (tmp_path / "kamiwaza.json").write_text(json.dumps(self._meta("2.1.0")))
        (tmp_path / "docker-compose.yml").write_text(
            "services:\n  app:\n    image: ghcr.io/x/y:2.0.14\n"
        )
        result = MetadataValidator().validate(Path(tmp_path / "kamiwaza.json"))
        assert any("docker-compose.yml" in w and "2.0.14" in w for w in result.warnings)

    def test_dockerfile_arg_drift_warns(self, tmp_path):
        from pathlib import Path
        from kamiwaza_extensions.validators.metadata import MetadataValidator

        (tmp_path / "kamiwaza.json").write_text(json.dumps(self._meta("2.1.0")))
        (tmp_path / "Dockerfile").write_text(
            "FROM python:3.11\nARG OMNIPARSE_VERSION=2.0.14\n"
        )
        result = MetadataValidator().validate(Path(tmp_path / "kamiwaza.json"))
        assert any("OMNIPARSE_VERSION" in w for w in result.warnings)

    def test_pyproject_drift_warns(self, tmp_path):
        from pathlib import Path
        from kamiwaza_extensions.validators.metadata import MetadataValidator

        (tmp_path / "kamiwaza.json").write_text(json.dumps(self._meta("2.1.0")))
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "2.0.14"\n'
        )
        result = MetadataValidator().validate(Path(tmp_path / "kamiwaza.json"))
        assert any("pyproject.toml" in w and "2.0.14" in w for w in result.warnings)

    def test_aligned_versions_no_drift_warnings(self, tmp_path):
        from pathlib import Path
        from kamiwaza_extensions.validators.metadata import MetadataValidator

        (tmp_path / "kamiwaza.json").write_text(
            json.dumps(self._meta("2.1.0", "ghcr.io/x/y:2.1.0"))
        )
        (tmp_path / "docker-compose.yml").write_text(
            "services:\n  app:\n    image: ghcr.io/x/y:2.1.0\n"
        )
        (tmp_path / "Dockerfile").write_text(
            "FROM python:3.11\nARG OMNIPARSE_VERSION=2.1.0\n"
        )
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "2.1.0"\n'
        )
        result = MetadataValidator().validate(Path(tmp_path / "kamiwaza.json"))
        drift = [w for w in result.warnings if "drift" in w.lower()]
        assert drift == []

    def test_unrelated_thirdparty_image_no_drift_warning(self, tmp_path):
        """A redis/postgres sidecar's semver tag must not trigger a drift
        warning when the extension declares its own image repo."""
        from pathlib import Path
        from kamiwaza_extensions.validators.metadata import MetadataValidator

        (tmp_path / "kamiwaza.json").write_text(
            json.dumps(self._meta("2.1.0", "ghcr.io/kamiwaza/app:2.1.0"))
        )
        (tmp_path / "docker-compose.yml").write_text(
            "services:\n"
            "  app:\n"
            "    image: ghcr.io/kamiwaza/app:2.1.0\n"
            "  redis:\n"
            "    image: redis:7.2.4\n"
        )
        result = MetadataValidator().validate(Path(tmp_path / "kamiwaza.json"))
        drift = [w for w in result.warnings if "drift" in w.lower()]
        assert drift == []
