from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from tests.e2e.extension_contract.contracts import ECHO_CHECK, AppSmokeContract
from tests.e2e.extension_contract.harness_test_helpers import settings_fixture
from tests.e2e.extension_contract.support import build_ops
from tests.e2e.extension_contract.support.harness import LiveExtensionHarness


def test_write_deployment_artifact(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LIVE_EXTENSION_OUTPUT_DIR", str(tmp_path))
    harness = LiveExtensionHarness(settings_fixture(), SimpleNamespace())
    deployment = {"id": "dep-123", "name": "echo-check-poc", "status": "DEPLOYED", "access_path": "/apps/dep-123"}

    artifact_path = harness.write_deployment_artifact(deployment, ECHO_CHECK)

    assert artifact_path == tmp_path / "echo-check.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["deployment_id"] == "dep-123"
    assert payload["app_url"] == "https://localhost/apps/dep-123"
    assert payload["readiness_url"] == "https://localhost/apps/dep-123/api/ready"
    assert payload["smoke_url"] == "https://localhost/apps/dep-123/api/runtime"


def test_write_deployment_artifact_defaults_to_repo_artifact_dir(monkeypatch) -> None:
    monkeypatch.delenv("LIVE_EXTENSION_OUTPUT_DIR", raising=False)
    harness = LiveExtensionHarness(settings_fixture(), SimpleNamespace())
    deployment = {"id": "dep-123", "name": "echo-check-poc", "status": "DEPLOYED", "access_path": "/apps/dep-123"}

    artifact_path = harness.write_deployment_artifact(deployment, ECHO_CHECK)

    assert artifact_path.name == "echo-check.json"
    assert artifact_path.parent.name == "live-extensions"
    assert artifact_path.parent.parent.name == ".artifacts"
    artifact_path.unlink()


def test_echo_check_contract_resolves_repo_version() -> None:
    metadata_path = Path(__file__).resolve().parent / "echo-check" / "kamiwaza.json"
    expected_version = json.loads(metadata_path.read_text(encoding="utf-8"))["version"]
    assert ECHO_CHECK.resolved_template_version() == expected_version


def test_find_app_template_uses_resolved_repo_version() -> None:
    expected_version = ECHO_CHECK.resolved_template_version()
    harness = LiveExtensionHarness(
        settings_fixture(),
        SimpleNamespace(get=lambda path, params=None: [{"name": "Echo Check", "version": expected_version}, {"name": "Echo Check", "version": "0.0.1"}]),
    )

    template = harness.find_app_template(ECHO_CHECK)

    assert template["version"] == expected_version


def test_build_extension_skips_when_neither_contract_nor_flag_request_build(monkeypatch) -> None:
    monkeypatch.delenv("LIVE_EXTENSION_BUILD_EXTENSIONS", raising=False)
    no_build_contract = replace(ECHO_CHECK, build_before_deploy=False)
    harness = LiveExtensionHarness(settings_fixture(), SimpleNamespace())
    commands: list[list[str]] = []
    monkeypatch.setattr("tests.e2e.extension_contract.support.build_ops.run_repo_command", lambda _h, command: commands.append(command))

    harness.build_extension(no_build_contract)

    assert commands == []


def test_build_extension_respects_force_flag(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_EXTENSION_BUILD_EXTENSIONS", "1")
    no_build_contract = replace(ECHO_CHECK, build_before_deploy=False)
    harness = LiveExtensionHarness(settings_fixture(), SimpleNamespace())
    commands: list[list[str]] = []
    monkeypatch.setattr("tests.e2e.extension_contract.support.build_ops.run_repo_command", lambda _h, command: commands.append(command))
    monkeypatch.setattr("tests.e2e.extension_contract.support.build_ops._align_built_images_to_template", lambda _h, _c: None)

    harness.build_extension(no_build_contract)

    assert len(commands) == 1
    docker_build = commands[0]
    assert docker_build[:2] == ["docker", "build"]


def test_build_extension_invokes_docker_build_for_each_service(monkeypatch) -> None:
    harness = LiveExtensionHarness(settings_fixture(), SimpleNamespace())
    commands: list[list[str]] = []
    monkeypatch.setattr("tests.e2e.extension_contract.support.build_ops.run_repo_command", lambda _h, command: commands.append(command))
    monkeypatch.setattr("tests.e2e.extension_contract.support.build_ops._align_built_images_to_template", lambda _h, _c: None)

    harness.build_extension(ECHO_CHECK)

    assert len(commands) >= 1
    for command in commands:
        assert command[:2] == ["docker", "build"]
        assert "-t" in command
        assert "-f" in command


def test_build_extension_retags_release_image_for_appgarden(monkeypatch) -> None:
    harness = LiveExtensionHarness(settings_fixture(), SimpleNamespace())
    commands: list[list[str]] = []
    extension_root = Path(__file__).resolve().parent / "echo-check"
    compose_images = {
        extension_root / "docker-compose.yml": {"app": "kamiwazaai/echo-check-app:0.2.0-dev"},
        extension_root / "docker-compose.appgarden.yml": {"app": "kamiwazaai/echo-check-app:0.2.0"},
    }
    build_targets = {
        "app": build_ops._BuildTarget(
            image="kamiwazaai/echo-check-app:0.2.0-dev",
            context=extension_root / "backend",
            dockerfile=extension_root / "backend" / "Dockerfile",
        ),
    }
    monkeypatch.setattr("tests.e2e.extension_contract.support.build_ops.run_repo_command", lambda _h, command: commands.append(command))
    monkeypatch.setattr("tests.e2e.extension_contract.support.build_ops._compose_service_images", compose_images.__getitem__)
    monkeypatch.setattr("tests.e2e.extension_contract.support.build_ops._compose_service_build_targets", lambda _path, _root: build_targets)
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    monkeypatch.setattr("tests.e2e.extension_contract.support.build_ops.image_exists_locally", lambda image: image == "kamiwazaai/echo-check-app:0.2.0-dev")

    harness.build_extension(ECHO_CHECK)

    tag_commands = [c for c in commands if c[:2] == ["docker", "tag"]]
    assert tag_commands == [["docker", "tag", "kamiwazaai/echo-check-app:0.2.0-dev", "kamiwazaai/echo-check-app:0.2.0"]]


def test_push_app_template_creates_when_missing(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_get(path, params=None):
        calls.append(("get", path))
        return []

    def fake_post(path, json=None):
        calls.append(("post", (path, json)))
        return {"id": "new-template"}

    client = SimpleNamespace(get=fake_get, post=fake_post, put=lambda *a, **kw: None)
    harness = LiveExtensionHarness(settings_fixture(username=None, password=None, api_key="pat-test"), client)

    harness.push_app_template(ECHO_CHECK)

    posted = next(c for c in calls if c[0] == "post")
    posted_path, posted_payload = posted[1]
    assert posted_path == "/apps/app_templates"
    assert posted_payload["name"] == "Echo Check"
    assert posted_payload["version"] == ECHO_CHECK.resolved_template_version()
    assert "compose_yml" in posted_payload and posted_payload["compose_yml"].strip()


def test_push_app_template_updates_when_present(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    existing_id = "tmpl-42"

    def fake_get(path, params=None):
        calls.append(("get", path))
        return [{"name": "Echo Check", "id": existing_id, "version": "0.0.0"}]

    def fake_put(path, json=None):
        calls.append(("put", (path, json)))
        return {"id": existing_id}

    client = SimpleNamespace(get=fake_get, post=lambda *a, **kw: None, put=fake_put)
    harness = LiveExtensionHarness(settings_fixture(username=None, password=None, api_key="pat-test"), client)

    harness.push_app_template(ECHO_CHECK)

    put_call = next(c for c in calls if c[0] == "put")
    put_path, put_payload = put_call[1]
    assert put_path == f"/apps/app_templates/{existing_id}"
    assert put_payload["name"] == "Echo Check"


def test_deployment_env_vars_include_platform_connection(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_EXTENSION_DEPLOY_ENV_EXTRA_FLAG", "enabled")
    harness = LiveExtensionHarness(settings_fixture(verify_ssl=True), SimpleNamespace())

    env_vars = harness.deployment_env_vars(ECHO_CHECK)

    assert env_vars["KAMIWAZA_API_URL"] == "https://localhost/api"
    assert env_vars["KAMIWAZA_VERIFY_SSL"] == "true"
    assert env_vars["EXTRA_FLAG"] == "enabled"


def test_deployment_env_vars_include_secret_encryption_key() -> None:
    contract = AppSmokeContract(
        extension_name="echo-check",
        template_name="Echo Check",
        secret_encryption_key_env_var="SECRET_ENCRYPTION_KEY",
    )
    harness = LiveExtensionHarness(settings_fixture(), SimpleNamespace())

    env_vars = harness.deployment_env_vars(contract)

    assert env_vars["SECRET_ENCRYPTION_KEY"] == harness.secret_encryption_key


def test_command_timeout_seconds_caps_and_defaults(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_EXTENSION_REPO_COMMAND_TIMEOUT", "999999")
    assert build_ops._command_timeout_seconds() == 3600

    monkeypatch.setenv("LIVE_EXTENSION_REPO_COMMAND_TIMEOUT", "not-a-number")
    assert build_ops._command_timeout_seconds() == 600
