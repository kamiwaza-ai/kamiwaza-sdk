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
        import typer

        from kamiwaza_extensions.commands.validate import run_validate

        (tmp_path / "kamiwaza.json").write_text("{bad json")

        with pytest.raises(typer.Exit):
            run_validate(path=str(tmp_path), json_output=True)

        output = json.loads(capsys.readouterr().out)
        assert output["passed"] is False
        assert output["errors"]

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
