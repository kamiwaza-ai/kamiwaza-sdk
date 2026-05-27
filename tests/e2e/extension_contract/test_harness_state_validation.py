from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e.extension_contract.conftest import deployed_app_contract
from tests.e2e.extension_contract.contracts import ECHO_CHECK
from tests.e2e.extension_contract.harness_test_helpers import bootstrap_state_fixture, settings_fixture
from tests.e2e.extension_contract.support import kubectl_secret_value, safe_token_file_path
from tests.e2e.extension_contract.support.harness import LiveExtensionHarness
from tests.e2e.extension_contract.support.state import LiveRoutedIntegrationState


def test_bootstrap_state_returns_none_when_pytest_fail_is_mocked(monkeypatch) -> None:
    monkeypatch.setenv("RUN_LIVE_EXTENSION_TESTS", "1")
    monkeypatch.setattr("tests.e2e.extension_contract.support.state_loader.bootstrap_state_candidates", lambda: (_ for _ in ()).throw(FileNotFoundError("missing bootstrap")))
    failures: list[str] = []
    monkeypatch.setattr("tests.e2e.extension_contract.support.state_loader.pytest.fail", lambda message: failures.append(str(message)))

    from tests.e2e.extension_contract.support.state_loader import load_live_routed_integration_state

    assert load_live_routed_integration_state() is None
    assert failures == ["missing bootstrap"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"api_base_url": "https://kamiwaza.test/api", "app_origin": "https://kamiwaza.test", "personas": ["bad-entry"]}, "contains non-object personas\\[0\\]"),
        ({"app_origin": "https://kamiwaza.test", "personas": []}, "missing api_base_url"),
        ({"api_base_url": "https://kamiwaza.test/api", "app_origin": "https://kamiwaza.test", "personas": [{"username": "admin", "credential_ref": "kamiwaza-user-admin"}]}, "missing role_key"),
        ({"api_base_url": "https://kamiwaza.test/api", "app_origin": "https://kamiwaza.test", "personas": [], "workrooms": ["bad-entry"]}, "invalid workrooms object"),
        ({"api_base_url": "https://kamiwaza.test/api", "app_origin": "https://kamiwaza.test", "personas": [], "workrooms": {"allowed_workroom_id": "not-a-uuid"}}, "invalid workroom id"),
        ({"api_base_url": "https://kamiwaza.test/api", "app_origin": "https://kamiwaza.test", "personas": [], "credential_resolution": ["bad-entry"]}, "invalid credential_resolution object"),
        ({"api_base_url": "https://kamiwaza.test/api", "app_origin": "https://kamiwaza.test", "personas": [], "discovered_models": "oops"}, "invalid discovered_models"),
    ],
)
def test_bootstrap_state_validation_errors(tmp_path: Path, payload: dict[str, object], message: str) -> None:
    path = tmp_path / "bootstrap-state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises((TypeError, ValueError), match=message):
        LiveRoutedIntegrationState.from_path(path)


def test_bootstrap_state_normalizes_workroom_ids_to_lowercase(tmp_path: Path) -> None:
    path = tmp_path / "bootstrap-state.json"
    path.write_text(json.dumps({"api_base_url": "https://kamiwaza.test/api", "app_origin": "https://kamiwaza.test", "personas": [], "workrooms": {"allowed_workroom_id": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"}}), encoding="utf-8")
    loaded = LiveRoutedIntegrationState.from_path(path)
    assert loaded.workrooms["allowed_workroom_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_bootstrap_state_exposes_immutable_mappings(tmp_path: Path) -> None:
    loaded = bootstrap_state_fixture(tmp_path)
    with pytest.raises(TypeError):
        loaded.credential_resolution["type"] = "token_file"  # type: ignore[index]


def test_kubectl_secret_value_rejects_invalid_secret_name(monkeypatch) -> None:
    monkeypatch.setattr("tests.e2e.extension_contract.support.common.shutil.which", lambda name: "/usr/bin/kubectl")
    with pytest.raises(ValueError, match="invalid secret_name"):
        kubectl_secret_value("--namespace=default", "kamiwaza", "password")


def test_kubectl_secret_value_rejects_invalid_namespace(monkeypatch) -> None:
    monkeypatch.setattr("tests.e2e.extension_contract.support.common.shutil.which", lambda name: "/usr/bin/kubectl")
    with pytest.raises(ValueError, match="invalid namespace"):
        kubectl_secret_value("kamiwaza-user-admin", "--context=prod", "password")


def test_kubectl_secret_value_uses_literal_jsonpath_for_dotted_keys(monkeypatch) -> None:
    recorded_commands: list[list[str]] = []
    monkeypatch.setattr("tests.e2e.extension_contract.support.common.shutil.which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr("tests.e2e.extension_contract.support.common.run_local_command", lambda command, **kwargs: recorded_commands.append(command) or SimpleNamespace(returncode=0, stdout="c2VjcmV0", stderr=""))
    assert kubectl_secret_value("kamiwaza-user-admin", "kamiwaza", "tls.crt") == "secret"
    assert recorded_commands[0][-1] == "jsonpath={.data['tls.crt']}"


def test_safe_token_file_path_rejects_traversal(tmp_path: Path) -> None:
    bootstrap_path = tmp_path / "bootstrap-state.json"
    bootstrap_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="outside allowed live-test roots"):
        safe_token_file_path("../../../../etc/passwd", bootstrap_path=bootstrap_path)


def test_harness_close_best_effort_closes_all_clients(caplog: pytest.LogCaptureFixture) -> None:
    closed: list[str] = []
    primary_client = SimpleNamespace(close=lambda: closed.append("primary"))
    primary_client._bootstrap_client = SimpleNamespace(close=lambda: closed.append("primary-bootstrap"))
    harness = LiveExtensionHarness(settings_fixture(), primary_client)
    harness.http = SimpleNamespace(close=lambda: (_ for _ in ()).throw(RuntimeError("http boom")))  # type: ignore[assignment]
    harness._persona_clients["viewer"] = SimpleNamespace(close=lambda: (_ for _ in ()).throw(RuntimeError("persona boom")))  # type: ignore[assignment]
    harness._bootstrap_clients["admin"] = SimpleNamespace(close=lambda: closed.append("bootstrap"))  # type: ignore[assignment]
    with caplog.at_level("WARNING"):
        harness.close()
    assert closed == ["primary", "primary-bootstrap", "bootstrap"]
    assert "Failed to close harness HTTP session" in caplog.text
    assert "Failed to close persona client" in caplog.text


def test_persona_missing_from_bootstrap_state_fails(tmp_path: Path) -> None:
    harness = LiveExtensionHarness(settings_fixture(bootstrap_state=bootstrap_state_fixture(tmp_path)), SimpleNamespace())
    harness.bootstrap_state = LiveRoutedIntegrationState(
        **{**bootstrap_state_fixture(tmp_path).__dict__, "personas": {}}
    )
    with pytest.raises(pytest.fail.Exception, match="Available personas"):
        harness.persona("allowed_non_admin")


def test_deployed_app_contract_cleans_up_when_setup_fails() -> None:
    cleaned: list[str] = []

    class HarnessStub:
        keep_deployments = False
        def build_extension(self, contract: object) -> None: return None
        def push_app_template(self, contract: object) -> None: return None
        def find_app_template(self, contract: object) -> dict[str, str]: return {"id": "tpl-123"}
        def pull_template_images(self, template_id: str) -> None: return None
        def deploy_app(self, template_id: str, contract: object) -> dict[str, str]: return {"id": "dep-123"}
        def wait_for_deployment(self, deployment_id: str) -> dict[str, str]: raise RuntimeError("deployment failed")
        def write_deployment_artifact(self, deployment: object, contract: object) -> None: return None
        def cleanup_deployment(self, deployment_id: str) -> None: cleaned.append(deployment_id)

    generator = deployed_app_contract.__wrapped__(HarnessStub(), ECHO_CHECK)  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="deployment failed"):
        next(generator)
    assert cleaned == ["dep-123"]


def test_live_routed_integration_state_does_not_swallow_internal_keyerror(
    monkeypatch,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "bootstrap-state.json"
    candidate.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("RUN_LIVE_EXTENSION_TESTS", "1")
    monkeypatch.setattr(
        "tests.e2e.extension_contract.support.state_loader.bootstrap_state_candidates",
        lambda: [candidate],
    )
    monkeypatch.setattr(
        "tests.e2e.extension_contract.support.state_loader.LiveRoutedIntegrationState.from_path",
        lambda path: (_ for _ in ()).throw(KeyError("internal parser bug")),
    )

    from tests.e2e.extension_contract.support.state_loader import load_live_routed_integration_state

    with pytest.raises(KeyError, match="internal parser bug"):
        load_live_routed_integration_state()


def test_live_routed_integration_state_reports_unreadable_candidate(
    monkeypatch,
) -> None:
    class BrokenCandidate:
        def exists(self) -> bool:
            raise PermissionError("permission denied")

        def __str__(self) -> str:
            return "broken-bootstrap-state.json"

    monkeypatch.setenv("RUN_LIVE_EXTENSION_TESTS", "1")
    monkeypatch.setattr(
        "tests.e2e.extension_contract.support.state_loader.bootstrap_state_candidates",
        lambda: [BrokenCandidate()],
    )

    from tests.e2e.extension_contract.support.state_loader import load_live_routed_integration_state

    with pytest.raises(pytest.fail.Exception, match="permission denied"):
        load_live_routed_integration_state()
