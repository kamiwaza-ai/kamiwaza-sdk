"""Hermetic contracts for live credential discovery and required auth coverage."""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.integration import conftest as live_conftest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never execute a workstation helper or issue a real password grant."""
    monkeypatch.setattr(live_conftest, "_LIVE_PASSWORD_CACHE", {})
    monkeypatch.setattr(
        live_conftest.subprocess,
        "run",
        Mock(side_effect=AssertionError("unexpected helper")),
    )
    monkeypatch.setattr(
        live_conftest,
        "_password_auth_works",
        Mock(side_effect=AssertionError("unexpected grant")),
    )
    monkeypatch.delenv("KAMIWAZA_PASSWORD", raising=False)


@pytest.mark.parametrize("password", ["", "   "])
def test_required_password_fails_instead_of_skipping(password: str) -> None:
    with pytest.raises(
        pytest.fail.Exception, match="Live password resolution failed"
    ) as exc:
        inspect.unwrap(live_conftest.live_password_required)(
            (password, "configured password rejected")
        )
    assert "configured password rejected" in str(exc.value)
    assert "KAMIWAZA_ROOT" in str(exc.value)
    assert "KAMIWAZA_PASSWORD" in str(exc.value)


def test_missing_diagnostic_does_not_print_none() -> None:
    with pytest.raises(pytest.fail.Exception, match="no password resolved"):
        inspect.unwrap(live_conftest.live_password_required)(("", None))


def test_required_password_returns_validated_credential() -> None:
    assert (
        inspect.unwrap(live_conftest.live_password_required)(("validated-secret", None))
        == "validated-secret"
    )


@pytest.mark.parametrize("api_key", ["", "pat"])
def test_missing_password_cannot_skip_selected_password_coverage(api_key: str) -> None:
    resolution = ("", "kz-login fallback unavailable; configured password is empty")
    if api_key:
        assert (
            inspect.unwrap(live_conftest.resolved_live_password)(resolution, api_key)
            == ""
        )
    else:
        with pytest.raises(pytest.fail.Exception):
            inspect.unwrap(live_conftest.resolved_live_password)(resolution, api_key)
    with pytest.raises(pytest.fail.Exception):
        inspect.unwrap(live_conftest.live_password_required)(resolution)


def test_pat_client_remains_usable_without_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_client = object()
    factory = Mock(return_value=expected_client)
    monkeypatch.setattr(live_conftest, "KamiwazaClient", factory)
    client = inspect.unwrap(live_conftest.live_kamiwaza_client)(
        "https://example.invalid/api", "pat", "", "admin"
    )
    assert client is expected_client
    factory.assert_called_once_with("https://example.invalid/api", api_key="pat")


@pytest.mark.parametrize("root_shape", ["sibling", "parent", "deploy"])
def test_kz_login_candidates_cover_supported_layouts(
    tmp_path: Path, root_shape: str
) -> None:
    sdk_root = tmp_path / "sdk"
    sibling = tmp_path / "deploy" / "scripts" / "kz-login"
    configured_root = tmp_path / "elsewhere"
    roots = {
        "sibling": None,
        "parent": str(configured_root),
        "deploy": str(configured_root / "deploy"),
    }
    candidates = live_conftest._kz_login_candidates(sdk_root, roots[root_shape])
    assert candidates[0] == sibling
    if root_shape != "sibling":
        assert configured_root / "deploy" / "scripts" / "kz-login" in candidates
    else:
        assert candidates == [sibling]


def test_kz_login_candidates_expand_home_and_deduplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    candidates = live_conftest._kz_login_candidates(tmp_path / "sdk", "~")
    assert candidates == [
        tmp_path / "deploy/scripts/kz-login",
        tmp_path / "scripts/kz-login",
    ]


@pytest.mark.parametrize(
    "helper_result", ["password", "empty", "exit", "timeout", "oserror"]
)
def test_helper_execution_is_bounded_and_hermetic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, helper_result: str
) -> None:
    script = tmp_path / "scripts/kz-login"
    script.parent.mkdir()
    script.touch()
    monkeypatch.setattr(
        live_conftest, "_kz_login_candidates", lambda *_: [tmp_path / "missing", script]
    )
    results = {
        "password": SimpleNamespace(stdout="unit-password\n"),
        "empty": SimpleNamespace(stdout="  \n"),
        "exit": subprocess.CalledProcessError(1, "kz-login", stderr="sensitive-output"),
        "timeout": subprocess.TimeoutExpired("kz-login", 15),
        "oserror": OSError("not executable"),
    }
    result = results[helper_result]
    runner = (
        Mock(side_effect=result)
        if isinstance(result, Exception)
        else Mock(return_value=result)
    )
    monkeypatch.setattr(live_conftest.subprocess, "run", runner)
    expected = "unit-password" if helper_result == "password" else None
    assert live_conftest._resolve_kz_login_password() == expected
    runner.assert_called_once_with(
        [str(script), "--show-password"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_deploy_root_is_used_when_sibling_helper_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk_root = tmp_path / "isolated/sdk"
    deploy_root = tmp_path / "deploy"
    script = deploy_root / "scripts/kz-login"
    script.parent.mkdir(parents=True)
    script.touch()
    monkeypatch.setattr(
        live_conftest, "__file__", str(sdk_root / "tests/integration/conftest.py")
    )
    monkeypatch.setenv("KAMIWAZA_ROOT", str(deploy_root))
    runner = Mock(return_value=SimpleNamespace(stdout="unit-password\n"))
    monkeypatch.setattr(live_conftest.subprocess, "run", runner)
    assert live_conftest._resolve_kz_login_password() == "unit-password"
    assert runner.call_args.args[0] == [str(script), "--show-password"]


@pytest.mark.parametrize(
    "fallback,configured,accepted,expected_calls",
    [
        ("kube", "config", "kube", ["kube"]),
        (None, "config", "config", ["config"]),
        ("stale", "config", "config", ["stale", "config"]),
        ("bad", "bad", None, ["bad"]),
        (None, "", None, []),
    ],
)
def test_resolution_validates_distinct_credentials_once(
    monkeypatch: pytest.MonkeyPatch, fallback, configured, accepted, expected_calls
) -> None:
    lookup = Mock(return_value=fallback)
    grant = Mock(
        side_effect=lambda _url, _user, password: (
            password == accepted,
            "grant rejected",
        )
    )
    monkeypatch.setattr(live_conftest, "_resolve_kz_login_password", lookup)
    monkeypatch.setattr(live_conftest, "_password_auth_works", grant)
    config = SimpleNamespace(getoption=lambda _: configured)
    resolve = inspect.unwrap(live_conftest.live_password_resolution)
    first = resolve("https://example.invalid/api", "admin", config)
    second = resolve("https://example.invalid/api", "admin", config)
    assert first == second
    assert first[0] == (accepted or "")
    assert [call.args[2] for call in grant.call_args_list] == expected_calls
    lookup.assert_called_once()
    if accepted:
        assert (
            inspect.unwrap(live_conftest.resolved_live_password)(first, "pat")
            == accepted
        )
        assert inspect.unwrap(live_conftest.live_password_required)(first) == accepted
    else:
        with pytest.raises(pytest.fail.Exception):
            inspect.unwrap(live_conftest.live_password_required)(first)


def test_missing_username_fails_without_reading_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup = Mock(side_effect=AssertionError("must not read secret"))
    monkeypatch.setattr(live_conftest, "_resolve_kz_login_password", lookup)
    result = live_conftest._resolve_live_password_once(
        live_server_available="https://example.invalid/api",
        live_username=" ",
        configured_password="configured",
    )
    with pytest.raises(pytest.fail.Exception, match="live username is empty"):
        inspect.unwrap(live_conftest.live_password_required)(result)
    lookup.assert_not_called()


pytest_plugins = ["pytester"]

_SESSION_CONFTEST = """
import pytest
from unittest.mock import Mock
from tests.integration import conftest as live
from tests.integration.conftest import (
    live_password_resolution, resolved_live_password, live_password_required,
)

@pytest.fixture(scope="session")
def live_server_available():
    return "https://example.invalid/api"

@pytest.fixture(scope="session")
def live_username():
    return "admin"

@pytest.fixture(scope="session")
def live_api_key():
    return "pat"

@pytest.fixture(scope="session", autouse=True)
def credentials():
    with pytest.MonkeyPatch.context() as patch:
        lookup = Mock(return_value=PASSWORD)
        grant = Mock(return_value=(True, ""))
        patch.setattr(live, "_LIVE_PASSWORD_CACHE", {})
        patch.setattr(live, "_resolve_kz_login_password", lookup)
        patch.setattr(live, "_password_auth_works", grant)
        patch.setenv("KAMIWAZA_PASSWORD", "")
        yield
        lookup.assert_called_once()
        assert grant.call_count == bool(PASSWORD)


def pytest_addoption(parser):
    parser.addoption("--live-password", default="")
"""


@pytest.mark.parametrize("password", [None, "resolved-secret"])
def test_pytest_session_preserves_password_coverage(
    pytester: pytest.Pytester, password: str | None
) -> None:
    pytester.makeconftest(f"PASSWORD = {password!r}\n" + _SESSION_CONFTEST)
    pytester.makepyfile("""
        def test_pat_client(resolved_live_password):
            assert isinstance(resolved_live_password, str)

        def test_password_grant(live_password_required):
            assert live_password_required

        def test_cli_login(live_password_required):
            assert live_password_required
    """)
    result = pytester.runpytest_inprocess("-q")
    if password:
        result.assert_outcomes(passed=3)
    else:
        result.assert_outcomes(passed=1, errors=2, skipped=0)
        result.stdout.fnmatch_lines(["*Live password resolution failed*"])
