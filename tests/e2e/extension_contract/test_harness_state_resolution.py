from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e.extension_contract.harness_test_helpers import bootstrap_state_fixture, state_with
from tests.e2e.extension_contract.support import (
    bootstrap_state_candidates,
    load_live_routed_integration_state,
    parse_password_output,
)
from tests.e2e.extension_contract.support.state import LivePersona, LiveRoutedIntegrationState


def test_bootstrap_state_resolve_password_parses_kz_login_output(monkeypatch, tmp_path: Path) -> None:
    helper_path = tmp_path / "kz-login"
    helper_path.write_text("#!/bin/sh\n", encoding="utf-8")
    state = state_with(tmp_path, credential_resolution={**bootstrap_state_fixture(tmp_path).credential_resolution, "helper": {"type": "kz_login", "path": str(helper_path)}})
    monkeypatch.setattr("tests.e2e.extension_contract.support.state.resolve_deploy_login", lambda bootstrap_path=None, preferred_path=None: preferred_path or helper_path)
    monkeypatch.setattr("tests.e2e.extension_contract.support.state.run_local_command", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Password: secret-value\n"))

    assert state.resolve_password(state.persona("admin")) == "secret-value"


def test_bootstrap_state_resolve_password_allows_directory_style_usernames(monkeypatch, tmp_path: Path) -> None:
    helper_path = tmp_path / "kz-login"
    helper_path.write_text("#!/bin/sh\n", encoding="utf-8")
    state = state_with(
        tmp_path,
        personas={**bootstrap_state_fixture(tmp_path).personas, "admin": LivePersona("admin", "john.doe", "kamiwaza-user-john-doe", None)},
        credential_resolution={**bootstrap_state_fixture(tmp_path).credential_resolution, "helper": {"type": "kz_login", "path": str(helper_path)}},
    )
    monkeypatch.setattr("tests.e2e.extension_contract.support.state.resolve_deploy_login", lambda bootstrap_path=None, preferred_path=None: preferred_path or helper_path)
    monkeypatch.setattr("tests.e2e.extension_contract.support.state.run_local_command", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="secret-value\n"))

    assert state.resolve_password(state.persona("admin")) == "secret-value"


def test_bootstrap_state_resolve_password_uses_helper_path_from_artifact(monkeypatch, tmp_path: Path) -> None:
    helper_path = tmp_path / "scripts" / "kz-login"
    helper_path.parent.mkdir(parents=True)
    helper_path.write_text("#!/bin/sh\n", encoding="utf-8")
    state = state_with(tmp_path, credential_resolution={**bootstrap_state_fixture(tmp_path).credential_resolution, "helper": {"type": "kz_login", "path": str(helper_path)}})
    seen_preferred: list[Path | None] = []
    monkeypatch.setattr(
        "tests.e2e.extension_contract.support.state.resolve_deploy_login",
        lambda bootstrap_path=None, preferred_path=None: seen_preferred.append(preferred_path) or preferred_path,
    )
    monkeypatch.setattr("tests.e2e.extension_contract.support.state.run_local_command", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Password: secret-value\n"))

    assert state.resolve_password(state.persona("admin")) == "secret-value"
    assert seen_preferred == [helper_path]


def test_bootstrap_state_resolve_password_uses_resolution_namespace_for_kz_login(
    monkeypatch, tmp_path: Path
) -> None:
    helper_path = tmp_path / "scripts" / "kz-login"
    helper_path.parent.mkdir(parents=True)
    helper_path.write_text("#!/bin/sh\n", encoding="utf-8")
    state = state_with(
        tmp_path,
        credential_resolution={
            **bootstrap_state_fixture(tmp_path).credential_resolution,
            "namespace": "platform-auth",
            "helper": {"type": "kz_login", "path": str(helper_path)},
        },
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        "tests.e2e.extension_contract.support.state.resolve_deploy_login",
        lambda bootstrap_path=None, preferred_path=None: preferred_path or helper_path,
    )
    monkeypatch.setattr(
        "tests.e2e.extension_contract.support.state.run_local_command",
        lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=0, stdout="secret-value\n"),
    )

    assert state.resolve_password(state.persona("admin")) == "secret-value"
    assert "--namespace" in commands[0]
    assert commands[0][commands[0].index("--namespace") + 1] == "platform-auth"


def test_bootstrap_state_resolve_password_accepts_frozen_helper_mapping(monkeypatch, tmp_path: Path) -> None:
    helper_path = tmp_path / "scripts" / "kz-login"
    helper_path.parent.mkdir(parents=True)
    helper_path.write_text("#!/bin/sh\n", encoding="utf-8")
    path = tmp_path / "bootstrap-state.json"
    payload = {
        "api_base_url": "https://kamiwaza.test/api",
        "app_origin": "https://kamiwaza.test",
        "verify_ssl": False,
        "namespace": "kamiwaza",
        "personas": [
            {
                "role_key": "admin",
                "username": "admin",
                "credential_ref": "kamiwaza-user-admin",
                "expected_workroom_role": None,
            }
        ],
        "workrooms": {},
        "credential_resolution": {
            "type": "secret_ref",
            "namespace": "kamiwaza",
            "key": "password",
            "helper": {"type": "kz_login", "path": str(helper_path)},
        },
        "generated_at": "2026-04-07T00:00:00+00:00",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    state = LiveRoutedIntegrationState.from_path(path)
    monkeypatch.setattr("tests.e2e.extension_contract.support.state.resolve_deploy_login", lambda bootstrap_path=None, preferred_path=None: preferred_path or helper_path)
    monkeypatch.setattr("tests.e2e.extension_contract.support.state.run_local_command", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Password: secret-value\n"))

    assert state.resolve_password(state.persona("admin")) == "secret-value"


def test_parse_password_output_accepts_raw_password_line() -> None:
    assert parse_password_output("secret-value\n") == "secret-value"


def test_parse_password_output_rejects_status_only_line() -> None:
    assert parse_password_output("Done.\n") is None


def test_bootstrap_state_resolve_api_key_from_token_file(tmp_path: Path) -> None:
    token_path = tmp_path / "token.txt"
    token_path.write_text("pat-token\n", encoding="utf-8")
    state = state_with(
        tmp_path,
        credential_resolution={"type": "token_file"},
        personas={**bootstrap_state_fixture(tmp_path).personas, "admin": LivePersona("admin", "admin", str(token_path), None)},
    )

    assert state.resolve_api_key(state.persona("admin")) == "pat-token"


def test_bootstrap_state_secret_ref_uses_resolution_namespace(monkeypatch, tmp_path: Path) -> None:
    state = state_with(tmp_path, credential_resolution={**bootstrap_state_fixture(tmp_path).credential_resolution, "namespace": "platform-auth"})
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr("tests.e2e.extension_contract.support.state.kubectl_secret_value", lambda secret_name, namespace, key: calls.append((secret_name, namespace, key)) or "secret")

    assert state.resolve_password(state.persona("admin")) == "secret"
    assert calls == [("kamiwaza-user-admin", "platform-auth", "password")]


def test_bootstrap_state_candidates_fail_fast_for_missing_explicit_path(monkeypatch, tmp_path: Path) -> None:
    missing_path = tmp_path / "definitely-missing-bootstrap-state.json"
    monkeypatch.setenv("LIVE_ROUTED_INTEGRATION_STATE", str(missing_path))

    with pytest.raises(FileNotFoundError, match="LIVE_ROUTED_INTEGRATION_STATE points to a missing file"):
        bootstrap_state_candidates()


def test_live_routed_integration_state_skips_lookup_when_live_tests_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RUN_LIVE_EXTENSION_TESTS", raising=False)
    monkeypatch.setenv("LIVE_ROUTED_INTEGRATION_STATE", str(tmp_path / "missing.json"))
    assert load_live_routed_integration_state() is None


def test_live_routed_integration_state_fails_when_no_bootstrap_candidate_exists(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RUN_LIVE_EXTENSION_TESTS", "1")
    monkeypatch.setattr("tests.e2e.extension_contract.support.state_loader.bootstrap_state_candidates", lambda: [tmp_path / "missing-bootstrap-state.json"])

    with pytest.raises(pytest.fail.Exception, match="no bootstrap state was found"):
        load_live_routed_integration_state()


def test_live_routed_integration_state_uses_next_valid_candidate(monkeypatch, tmp_path: Path) -> None:
    invalid_path = tmp_path / "invalid-bootstrap-state.json"
    invalid_path.write_text('{"api_base_url": ""}', encoding="utf-8")
    valid_path = tmp_path / "valid-bootstrap-state.json"
    valid_state = bootstrap_state_fixture(tmp_path)
    valid_payload = {
        "api_base_url": valid_state.api_base_url,
        "app_origin": valid_state.app_origin,
        "verify_ssl": valid_state.verify_ssl,
        "namespace": valid_state.namespace,
        "personas": [persona.__dict__ for persona in valid_state.personas.values()],
        "workrooms": dict(valid_state.workrooms),
        "discovered_models": [dict(model) for model in valid_state.discovered_models],
        "credential_resolution": {
            **dict(valid_state.credential_resolution),
            "helper": dict(valid_state.credential_resolution["helper"]),
        },
        "generated_at": valid_state.generated_at,
    }
    valid_path.write_text(json.dumps(valid_payload), encoding="utf-8")
    monkeypatch.setenv("RUN_LIVE_EXTENSION_TESTS", "1")
    monkeypatch.setattr("tests.e2e.extension_contract.support.state_loader.bootstrap_state_candidates", lambda: [invalid_path, valid_path])

    loaded = load_live_routed_integration_state()

    assert loaded is not None
    assert loaded.api_base_url == valid_state.api_base_url


def test_resolve_deploy_login_ignores_untrusted_bootstrap_helper(monkeypatch, tmp_path: Path) -> None:
    trusted_helper = tmp_path / "deploy" / "scripts" / "kz-login"
    trusted_helper.parent.mkdir(parents=True)
    trusted_helper.write_text("#!/bin/sh\n", encoding="utf-8")
    untrusted_helper = tmp_path / "tmp" / "kz-login"
    untrusted_helper.parent.mkdir(parents=True)
    untrusted_helper.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(
        "tests.e2e.extension_contract.support.common.REPO_ROOT",
        tmp_path / "kamiwaza",
    )

    from tests.e2e.extension_contract.support.common import resolve_deploy_login

    assert resolve_deploy_login(preferred_path=untrusted_helper) == trusted_helper.resolve()


def test_resolve_deploy_login_accepts_home_installed_helper(monkeypatch, tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    home_helper = home_dir / ".kamiwaza" / "scripts" / "kz-login"
    home_helper.parent.mkdir(parents=True)
    home_helper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("tests.e2e.extension_contract.support.common.REPO_ROOT", tmp_path / "kamiwaza")
    monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)

    from tests.e2e.extension_contract.support.common import resolve_deploy_login

    assert resolve_deploy_login() == home_helper.resolve()


def test_resolve_deploy_login_ignores_untrusted_env_path(monkeypatch, tmp_path: Path) -> None:
    trusted_helper = tmp_path / "deploy" / "scripts" / "kz-login"
    trusted_helper.parent.mkdir(parents=True)
    trusted_helper.write_text("#!/bin/sh\n", encoding="utf-8")
    untrusted_helper = tmp_path / "tmp" / "kz-login"
    untrusted_helper.parent.mkdir(parents=True)
    untrusted_helper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        "tests.e2e.extension_contract.support.common.REPO_ROOT",
        tmp_path / "kamiwaza",
    )
    monkeypatch.setenv("LIVE_EXTENSION_KZ_LOGIN_PATH", str(untrusted_helper))

    from tests.e2e.extension_contract.support.common import resolve_deploy_login

    assert resolve_deploy_login() == trusted_helper.resolve()
