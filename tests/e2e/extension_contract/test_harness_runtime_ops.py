from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from tests.e2e.extension_contract.harness_test_helpers import bootstrap_state_fixture, settings_fixture, state_with
from tests.e2e.extension_contract.support.harness import LiveExtensionHarness
from tests.e2e.extension_contract.support.settings import LiveExtensionSettings, assert_origin_ready


def test_deployment_diagnostics_requires_status_and_access_path() -> None:
    harness = LiveExtensionHarness(settings_fixture(), SimpleNamespace(get=lambda path: {"id": "dep-123", "status": "", "access_path": ""}))

    with pytest.raises(pytest.fail.Exception, match="incomplete"):
        harness.deployment_diagnostics("dep-123")


def test_wait_for_deployment_logs_returns_matching_payload() -> None:
    harness = LiveExtensionHarness(
        settings_fixture(probe_timeout_seconds=1),
        SimpleNamespace(get=lambda path: {"logs": ["marker req-123"], "total_lines": 1}),
    )

    payload = harness.wait_for_deployment_logs("dep-123", marker="marker", request_id="req-123")

    assert payload["total_lines"] == 1


def test_wait_for_deployment_retries_transient_request_errors(monkeypatch) -> None:
    responses = iter(
        [
            requests.ConnectionError("temporary outage"),
            {"status": "DEPLOYED", "access_path": "/apps/dep-123"},
        ]
    )
    harness = LiveExtensionHarness(
        settings_fixture(deployment_timeout_seconds=1),
        SimpleNamespace(),
    )
    def fake_get_deployment(_harness, _deployment_id):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(
        "tests.e2e.extension_contract.support.runtime_ops.get_deployment",
        fake_get_deployment,
    )
    monkeypatch.setattr("tests.e2e.extension_contract.support.runtime_ops.time.sleep", lambda _seconds: None)

    payload = harness.wait_for_deployment("dep-123")

    assert payload["status"] == "DEPLOYED"


def test_pull_template_images_requires_explicit_local_fallback(monkeypatch) -> None:
    harness = LiveExtensionHarness(settings_fixture(), SimpleNamespace(post=lambda path: {"all_successful": False, "results": [{"image": "missing", "success": False}]}))
    monkeypatch.setattr("tests.e2e.extension_contract.support.build_ops.image_exists_locally", lambda image: True)

    with pytest.raises(pytest.fail.Exception, match="Template image pull failed"):
        harness.pull_template_images("tpl-123")


def test_pull_template_images_allows_explicit_local_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_EXTENSION_ALLOW_LOCAL_IMAGE_FALLBACK", "1")
    harness = LiveExtensionHarness(settings_fixture(), SimpleNamespace(post=lambda path: {"all_successful": False, "results": [{"image": "missing", "success": False}]}))
    monkeypatch.setattr("tests.e2e.extension_contract.support.build_ops.image_exists_locally", lambda image: True)

    harness.pull_template_images("tpl-123")


def test_live_extension_settings_uses_bootstrap_state_defaults(monkeypatch, tmp_path) -> None:
    # Clear every env var LiveExtensionSettings.from_env() reads — otherwise
    # the live workflow (which sets KAMIWAZA_BASE_URL + KAMIWAZA_API_KEY for
    # the whole pytest run) makes these isolation tests pick up workflow
    # secrets and assert against the wrong base URL.
    monkeypatch.delenv("KAMIWAZA_API_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_BASE_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_API_KEY", raising=False)
    monkeypatch.delenv("KAMIWAZA_APP_ORIGIN", raising=False)
    monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("KAMIWAZA_USERNAME", raising=False)
    monkeypatch.delenv("KAMIWAZA_PASSWORD", raising=False)
    monkeypatch.setattr("tests.e2e.extension_contract.support.settings.load_local_admin_password", lambda: None)
    monkeypatch.setattr(
        "tests.e2e.extension_contract.support.state.LiveRoutedIntegrationState.resolve_password",
        lambda self, persona: "secret" if persona.role_key == "admin" else None,
    )

    settings = LiveExtensionSettings.from_env(bootstrap_state_fixture(tmp_path))

    assert settings.base_url == "https://kamiwaza.test/api"
    assert settings.origin == "https://kamiwaza.test"
    assert settings.username == "admin"
    assert settings.password is None or settings.password


def test_live_extension_settings_accepts_auth_fronted_ping(monkeypatch, tmp_path) -> None:
    state = bootstrap_state_fixture(tmp_path)
    monkeypatch.delenv("KAMIWAZA_BASE_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_API_KEY", raising=False)
    monkeypatch.setattr("tests.e2e.extension_contract.support.settings.load_local_admin_password", lambda: None)
    monkeypatch.setenv("KAMIWAZA_USERNAME", "admin")
    monkeypatch.setenv("KAMIWAZA_PASSWORD", "secret")
    settings = LiveExtensionSettings.from_env(state)
    assert settings.base_url == state.api_base_url


def test_live_extension_settings_rejects_unexpected_ping_401(monkeypatch) -> None:
    settings = settings_fixture(origin="https://kamiwaza.test")
    responses = {
        "https://kamiwaza.test/health": SimpleNamespace(status_code=404, text="missing"),
        "https://kamiwaza.test/": SimpleNamespace(status_code=404, text="missing"),
        "https://localhost/api/health": SimpleNamespace(status_code=404, text="missing"),
        "https://localhost/api/ping": SimpleNamespace(status_code=401, text="forbidden"),
    }
    monkeypatch.setattr("tests.e2e.extension_contract.support.settings.requests.get", lambda url, *args, **kwargs: responses[str(url)])

    with pytest.raises(pytest.fail.Exception, match="Kamiwaza origin health failed"):
        assert_origin_ready(settings)


def test_live_extension_settings_does_not_promote_allowed_non_admin(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("KAMIWAZA_API_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_BASE_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_API_KEY", raising=False)
    monkeypatch.delenv("KAMIWAZA_APP_ORIGIN", raising=False)
    monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("KAMIWAZA_USERNAME", raising=False)
    monkeypatch.delenv("KAMIWAZA_PASSWORD", raising=False)
    monkeypatch.setattr("tests.e2e.extension_contract.support.settings.load_local_admin_password", lambda: None)

    settings = LiveExtensionSettings.from_env(
        state_with(
            tmp_path,
            personas={"allowed_non_admin": bootstrap_state_fixture(tmp_path).personas["allowed_non_admin"]},
        )
    )

    assert settings.username is None
    assert settings.password is None
    assert settings.control_plane_role_key is None


def test_origin_preflight_requires_health_200(monkeypatch) -> None:
    settings = settings_fixture(origin="https://kamiwaza.test")
    responses = {
        "https://kamiwaza.test/health": SimpleNamespace(status_code=503, text="bad"),
        "https://kamiwaza.test/": SimpleNamespace(status_code=503, text="bad"),
        "https://localhost/api/health": SimpleNamespace(status_code=503, text="bad"),
        "https://localhost/api/ping": SimpleNamespace(status_code=503, text="bad"),
    }
    monkeypatch.setattr("tests.e2e.extension_contract.support.settings.requests.get", lambda url, *args, **kwargs: responses[str(url)])

    with pytest.raises(pytest.fail.Exception, match="Kamiwaza origin health failed"):
        assert_origin_ready(settings)


def test_origin_preflight_accepts_api_health_when_origin_is_unavailable(monkeypatch) -> None:
    settings = settings_fixture(origin="https://kamiwaza.test")
    responses = {
        "https://kamiwaza.test/health": SimpleNamespace(status_code=404, text="missing"),
        "https://kamiwaza.test/": SimpleNamespace(status_code=404, text="missing"),
        "https://localhost/api/health": SimpleNamespace(status_code=200, text="ok"),
        "https://localhost/api/ping": SimpleNamespace(status_code=401, text='{\"detail\":\"Not authenticated\"}'),
    }
    monkeypatch.setattr("tests.e2e.extension_contract.support.settings.requests.get", lambda url, *args, **kwargs: responses.get(str(url), SimpleNamespace(status_code=503, text="bad")))

    assert_origin_ready(settings)


def test_origin_preflight_accepts_origin_health(monkeypatch) -> None:
    settings = settings_fixture(origin="https://kamiwaza.test")
    monkeypatch.setattr("tests.e2e.extension_contract.support.settings.requests.get", lambda url, *args, **kwargs: SimpleNamespace(status_code=200, text="ok") if str(url) == "https://kamiwaza.test/health" else SimpleNamespace(status_code=503, text="bad"))

    assert_origin_ready(settings)


def test_live_extension_settings_falls_back_on_invalid_timeouts(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("KAMIWAZA_BASE_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_API_KEY", raising=False)
    monkeypatch.setenv("LIVE_EXTENSION_DEPLOY_TIMEOUT", "not-a-number")
    monkeypatch.setenv("LIVE_EXTENSION_PROBE_TIMEOUT", "still-bad")
    monkeypatch.setattr("tests.e2e.extension_contract.support.settings.load_local_admin_password", lambda: None)

    settings = LiveExtensionSettings.from_env(bootstrap_state_fixture(tmp_path))

    assert settings.deployment_timeout_seconds == 900
    assert settings.probe_timeout_seconds == 180
