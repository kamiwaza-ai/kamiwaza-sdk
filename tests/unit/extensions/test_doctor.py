"""Tests for DoctorChecker."""

import json
from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_extensions.doctor import DoctorChecker


@pytest.mark.unit
class TestDoctorSystemChecks:
    @pytest.fixture
    def checker(self, tmp_path):
        return DoctorChecker(config_dir=tmp_path / ".kamiwaza")

    def test_python_version_pass(self, checker):
        result = checker._check_python_version()
        assert result.status == "pass"  # We're running on >= 3.10

    def test_docker_installed_pass(self, checker):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Docker version 24.0.0, build abc123"
            )
            result = checker._check_docker_installed()
            assert result.status == "pass"

    def test_docker_installed_fail(self, checker):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = checker._check_docker_installed()
            assert result.status == "fail"
            assert result.fix is not None

    def test_docker_running_pass(self, checker):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = checker._check_docker_running()
            assert result.status == "pass"

    def test_docker_running_fail(self, checker):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = checker._check_docker_running()
            assert result.status == "fail"

    def test_compose_v2_pass(self, checker):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Docker Compose v2.24.0"
            )
            result = checker._check_compose()
            assert result.status == "pass"


@pytest.mark.unit
class TestDoctorConnectionChecks:
    def test_no_connection_configured(self, tmp_path):
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        result = checker._check_connection()
        assert result.status == "warn"
        assert "No connection" in result.message


@pytest.mark.unit
class TestDoctorRegistryChecks:
    def test_registry_endpoint_flags_html_response(self):
        checker = DoctorChecker(config_dir=None)
        response = MagicMock(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            text='<!DOCTYPE html><html lang="en">',
        )
        with patch("requests.get", return_value=response):
            result = checker._check_registry_http_endpoint(
                "Registry image endpoint",
                "registry.kamiwaza.test",
            )

        # Both probe URLs (HTTPS first for non-loopback, then HTTP) returned
        # HTML, so the registry is unambiguously not serving /v2/ on either
        # scheme — that's a hard fail with a registry-auth exit code.
        assert result.status == "fail"
        assert "did not serve a registry /v2/ endpoint" in result.message
        assert result.exit_code == 20
        assert result.fix is not None
        assert "KAMIWAZA_REGISTRY" in result.fix

    def test_registry_endpoint_prefers_https_when_http_serves_html(self):
        """HTTPS-only registries with an HTML landing page on :80 must not
        be misreported as broken — the HTTPS probe should still succeed."""

        checker = DoctorChecker(config_dir=None)
        html = MagicMock(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<!DOCTYPE html>",
        )
        v2_ok = MagicMock(
            status_code=200,
            headers={"content-type": "application/json"},
            text="{}",
        )
        # Non-loopback host -> HTTPS probed first; both schemes use the same
        # patched ``requests.get``. The HTTPS probe returns v2_ok and short-
        # circuits before HTTP is tried.
        with patch("requests.get", side_effect=[v2_ok, html]):
            result = checker._check_registry_http_endpoint(
                "Registry image endpoint",
                "registry.kamiwaza.test",
            )

        assert result.status == "pass"
        assert "https://registry.kamiwaza.test/v2/" in result.message

    def test_registry_endpoint_loopback_uses_http_first_and_skips_verify(self):
        """Loopback dev registries (k0s/kind) speak plain HTTP and almost
        always present a self-signed cert if HTTPS is even reachable. Probe
        order is HTTP-first, and the HTTPS fallback must pass ``verify=False``
        without leaking ``InsecureRequestWarning`` to stderr."""

        import warnings

        checker = DoctorChecker(config_dir=None)
        ok = MagicMock(
            status_code=200,
            headers={"content-type": "application/json"},
            text="{}",
        )

        with patch("requests.get", return_value=ok) as mock_get:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = checker._check_registry_http_endpoint(
                    "Registry image endpoint",
                    "127.0.0.1:30010",
                )

        assert result.status == "pass"
        # HTTP probed first for loopback, succeeds, never falls back to HTTPS.
        assert mock_get.call_args.args[0].startswith("http://127.0.0.1")
        assert not any(
            "InsecureRequestWarning" in str(w.message) for w in caught
        )

    def test_registry_endpoint_loopback_https_fallback_skips_verify(self):
        """jxstanford Medium #2: the HTTP-success short-circuit in the
        prior test meant the ``verify=False`` HTTPS branch was never
        actually exercised. Force HTTP to fail so the HTTPS fallback
        runs, and assert it passed ``verify=False`` without emitting
        ``InsecureRequestWarning``."""

        import warnings

        import requests

        checker = DoctorChecker(config_dir=None)
        https_ok = MagicMock(
            status_code=200,
            headers={"content-type": "application/json"},
            text="{}",
        )
        # First call (HTTP) raises ConnectionError; second call (HTTPS) returns OK.
        with patch(
            "requests.get",
            side_effect=[requests.ConnectionError("conn refused"), https_ok],
        ) as mock_get:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = checker._check_registry_http_endpoint(
                    "Registry image endpoint",
                    "127.0.0.1:30010",
                )

        assert result.status == "pass"
        assert mock_get.call_count == 2
        # Second call is the HTTPS fallback; must be verify=False.
        second_call = mock_get.call_args_list[1]
        assert second_call.args[0].startswith("https://127.0.0.1")
        assert second_call.kwargs.get("verify") is False
        # Even with verify=False, no warning leaks to the user's stderr.
        assert not any(
            "InsecureRequestWarning" in str(w.message) for w in caught
        )

    def test_registry_endpoint_honors_connection_verify_ssl(self):
        """jxstanford Medium #1: a connection with verify_ssl=False (dev
        TLD auto-disable, etc.) probing a non-loopback HTTPS registry
        with a self-signed cert should pass — doctor must mirror what
        the rest of the SDK will do for the same cluster."""

        checker = DoctorChecker(config_dir=None)
        ok = MagicMock(
            status_code=200,
            headers={"content-type": "application/json"},
            text="{}",
        )
        with patch(
            "requests.get",
            side_effect=[
                # First call is HTTPS (non-loopback first); verify=False
                # because connection.verify_ssl=False.
                ok,
            ],
        ) as mock_get:
            result = checker._check_registry_http_endpoint(
                "Registry image endpoint",
                "registry.dev.test",
                connection_verify_ssl=False,
            )

        assert result.status == "pass"
        first_call = mock_get.call_args_list[0]
        assert first_call.args[0].startswith("https://")
        assert first_call.kwargs.get("verify") is False

    def test_registry_endpoint_accepts_4xx_with_bearer_challenge(self):
        """jxstanford Medium #3: some registries (GHCR with catalog
        disabled) return 4xx on /v2/ but ``WWW-Authenticate: Bearer``
        proves a registry is behind the URL — that's pass-worthy."""

        checker = DoctorChecker(config_dir=None)
        bearer = MagicMock(
            status_code=403,
            headers={
                "content-type": "application/json",
                "WWW-Authenticate": 'Bearer realm="https://auth.example/token"',
            },
            text="{}",
        )
        with patch("requests.get", return_value=bearer):
            result = checker._check_registry_http_endpoint(
                "Registry image endpoint",
                "ghcr.example",
            )

        assert result.status == "pass"

    def test_registry_endpoint_accepts_v2_json_response(self):
        checker = DoctorChecker(config_dir=None)
        response = MagicMock(
            status_code=200,
            headers={"content-type": "application/json; charset=utf-8"},
            text="{}",
        )
        with patch("requests.get", return_value=response):
            result = checker._check_registry_http_endpoint(
                "Registry image endpoint",
                "127.0.0.1:30010",
            )

        assert result.status == "pass"

    def test_push_endpoint_warns_on_docker_internal_alias(self):
        """``host.docker.internal`` only resolves inside the Docker VM, so
        the host-side probe is meaningless. Doctor should emit a clear
        ``warn`` rather than a misleading hard failure."""

        checker = DoctorChecker(config_dir=None)
        result = checker._check_push_registry_endpoint(
            "host.docker.internal:30010"
        )
        assert result.status == "warn"
        assert "Docker Desktop" in result.message

    @patch(
        "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
        return_value="podman-machine-default",
    )
    def test_push_endpoint_passes_when_podman_ssh_curl_succeeds(self, _mock_machine):
        """jxstanford Medium #5: cover the success path of the
        ``podman machine ssh ... curl /v2/`` probe. Until this test, the
        most complex branch in ``_check_push_registry_endpoint`` had no
        coverage at all."""

        checker = DoctorChecker(config_dir=None)
        success = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=success) as mock_run:
            result = checker._check_push_registry_endpoint(
                "host.containers.internal:30010"
            )

        assert result.status == "pass"
        assert "podman-machine-default" in result.message
        # The probe runs exactly one ``podman machine ssh`` invocation.
        cmd_args = mock_run.call_args.args[0]
        assert cmd_args[:4] == ["podman", "machine", "ssh", "podman-machine-default"]
        assert "curl" in cmd_args

    @patch(
        "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
        return_value="podman-machine-default",
    )
    def test_push_endpoint_fails_with_exit_code_when_podman_ssh_curl_fails(
        self, _mock_machine
    ):
        """jxstanford Medium #5 (negative path): a non-zero exit from the
        VM-side curl is a hard fail with the REGISTRY_AUTH exit code."""

        checker = DoctorChecker(config_dir=None)
        failure = MagicMock(returncode=22, stdout="", stderr="curl: (7) refused")
        with patch("subprocess.run", return_value=failure):
            result = checker._check_push_registry_endpoint(
                "host.containers.internal:30010"
            )

        assert result.status == "fail"
        assert result.exit_code == 20  # REGISTRY_AUTH
        assert "refused" in (result.fix or "")

    @patch(
        "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
        return_value=None,
    )
    def test_push_endpoint_warns_on_containers_internal_without_machine(
        self, _mock_machine
    ):
        """``host.containers.internal`` is a Podman VM alias; if no machine
        is running, the alias is unresolvable from the host and a probe
        would be just noise — emit a targeted warn instead."""

        checker = DoctorChecker(config_dir=None)
        result = checker._check_push_registry_endpoint(
            "host.containers.internal:30010"
        )
        assert result.status == "warn"
        assert "Podman" in result.message

    @patch("kamiwaza_extensions.registry_resolution.detect_core_config_registry")
    def test_registry_readiness_reports_split(self, mock_core, tmp_path, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_PUSH_REGISTRY", "host.containers.internal:30010")
        mock_core.return_value = "127.0.0.1:30010"
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        # Connection mock needs an explicit ``verify_ssl`` attribute (not a
        # MagicMock auto-attr) so the new probe TLS-verify policy
        # (jxstanford Medium #1) sees a real bool rather than truthy mock.
        checker._conn_mgr.get_active_connection = MagicMock(
            return_value=MagicMock(url="https://kamiwaza.test/api", verify_ssl=True)
        )
        ok = MagicMock(
            status_code=200,
            headers={"content-type": "application/json"},
            text="{}",
        )
        with (
            patch("requests.get", return_value=ok),
            patch(
                "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
                return_value=False,
            ),
            # Pretend docker accepts the alias as insecure — keeps the new
            # "Docker insecure-registries" check out of the result set so
            # this test continues to exercise just the split-reporting path.
            patch(
                "kamiwaza_extensions.registry_resolution.docker_accepts_insecure_push_to",
                return_value=True,
            ),
        ):
            results = checker._check_registry_readiness()

        assert results[0].name == "Registry resolution"
        assert "push=host.containers.internal:30010" in results[0].message
        assert {r.name for r in results} == {
            "Registry resolution",
            "Registry image endpoint",
            "Registry push endpoint",
        }

    @patch("kamiwaza_extensions.registry_resolution.detect_core_config_registry")
    def test_registry_readiness_fails_on_missing_insecure_registries(
        self, mock_core, tmp_path
    ):
        """jxstanford iter-4 Critical #1: when docker is the active push
        engine and the auto-rewritten alias isn't in ``insecure-registries``,
        doctor must emit a hard fail with the daemon.json fix so the user
        sees it before ``kz-ext dev`` push fails with a confusing TLS error."""

        mock_core.return_value = "127.0.0.1:30010"
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        checker._conn_mgr.get_active_connection = MagicMock(
            return_value=MagicMock(url="https://kamiwaza.test/api", verify_ssl=True)
        )
        ok = MagicMock(
            status_code=200,
            headers={"content-type": "application/json"},
            text="{}",
        )
        # Force the alias-rewrite path: VM in play, docker is the working
        # engine, but docker's RegistryConfig doesn't list the alias as
        # insecure (default Docker Desktop).
        with (
            patch("requests.get", return_value=ok),
            patch(
                "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._docker_is_working",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._has_podman",
                return_value=False,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.docker_accepts_insecure_push_to",
                return_value=False,
            ),
        ):
            results = checker._check_registry_readiness()

        insecure_check = next(
            (r for r in results if r.name == "Docker insecure-registries"), None
        )
        assert insecure_check is not None
        assert insecure_check.status == "fail"
        assert "insecure-registries" in (insecure_check.fix or "")
        assert "host.docker.internal" in (insecure_check.fix or "") or \
            "30010" in (insecure_check.fix or "")


@pytest.mark.unit
class TestDoctorExtensionChecks:
    def test_cli_version_compatible(self, tmp_path):
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        result = checker._check_cli_version(">=0.1.0,<1.0.0")
        assert result.status == "pass"

    def test_cli_version_incompatible(self, tmp_path):
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        result = checker._check_cli_version(">=99.0.0")
        assert result.status == "fail"

    def test_python_runtime_lib_found(self, tmp_path):
        req_file = tmp_path / "requirements.txt"
        # Use a range that fits the current compat bundle window
        # (`>=0.4,<0.5` for Python at time of writing — round-9 raised
        # the floor when ``_url`` was promoted to a public ``url``
        # module that scaffolded extensions import). PR-86 H7/M6 made
        # this check range-vs-range accurate — anything below 0.4 will
        # now correctly warn as below the floor.
        req_file.write_text("kamiwaza-extensions-lib>=0.4.0,<0.5\nfastapi\n")
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        result = checker._check_python_runtime_lib(req_file)
        assert result.status == "pass"

    def test_python_runtime_lib_missing(self, tmp_path):
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("fastapi\nuvicorn\n")
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        result = checker._check_python_runtime_lib(req_file)
        assert result.status == "warn"

    def test_ts_runtime_lib_found(self, tmp_path):
        pkg_file = tmp_path / "package.json"
        pkg_file.write_text(
            json.dumps({"dependencies": {"@kamiwaza-ai/extensions-lib": "^0.4.0"}})
        )
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        result = checker._check_ts_runtime_lib(pkg_file)
        assert result.status == "pass"

    def test_ts_runtime_lib_missing(self, tmp_path):
        pkg_file = tmp_path / "package.json"
        pkg_file.write_text(json.dumps({"dependencies": {"react": "^18.0.0"}}))
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        result = checker._check_ts_runtime_lib(pkg_file)
        assert result.status == "warn"


@pytest.mark.unit
class TestDoctorRunAll:
    def test_run_all_returns_results(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        checker = DoctorChecker(config_dir=tmp_path / ".kamiwaza")
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            results = checker.run_all()
        assert len(results) >= 4  # At least system checks + connection
        assert all(hasattr(r, "status") for r in results)


@pytest.mark.unit
class TestDoctorCommandExitCodePrecedence:
    """Review re-review PR #84 M2: an explicit ``CheckResult.exit_code``
    (e.g. CLUSTER_NOT_READY=23) must win over the generic FAILURE
    fallback even when an exit-code-less generic failure happens earlier
    in the run. The previous implementation locked in the first
    failure's code, masking the more-specific signal."""

    def test_explicit_exit_code_wins_over_earlier_generic_failure(self, monkeypatch):
        from typer.testing import CliRunner

        from kamiwaza_extensions.cli import app
        from kamiwaza_extensions.doctor import CheckResult

        # Build a fake DoctorChecker that returns: a generic failure first,
        # then a structured CLUSTER_NOT_READY=23 failure.
        results = [
            CheckResult("Docker installed", "fail", "Not found", fix="x"),
            CheckResult(
                "Cluster extension readiness",
                "fail",
                "CRD missing",
                fix="reinstall",
                exit_code=23,
            ),
        ]

        class FakeChecker:
            def __init__(self, *a, **kw):
                pass

            def run_all(self):
                return results

        monkeypatch.setattr(
            "kamiwaza_extensions.doctor.DoctorChecker",
            FakeChecker,
        )

        runner = CliRunner()
        result = runner.invoke(app, ["doctor"])
        # CLUSTER_NOT_READY (23) wins over the generic 1 from "Docker
        # installed" failing first.
        assert result.exit_code == 23

    def test_falls_back_to_failure_when_no_explicit_exit_code(self, monkeypatch):
        from typer.testing import CliRunner

        from kamiwaza_extensions.cli import app
        from kamiwaza_extensions.doctor import CheckResult

        results = [CheckResult("Docker installed", "fail", "Not found", fix="x")]

        class FakeChecker:
            def __init__(self, *a, **kw):
                pass

            def run_all(self):
                return results

        monkeypatch.setattr(
            "kamiwaza_extensions.doctor.DoctorChecker",
            FakeChecker,
        )

        runner = CliRunner()
        result = runner.invoke(app, ["doctor"])
        # Generic FAILURE fallback when no check carries an explicit code.
        assert result.exit_code == 1

    def test_no_exit_when_all_checks_pass(self, monkeypatch):
        from typer.testing import CliRunner

        from kamiwaza_extensions.cli import app
        from kamiwaza_extensions.doctor import CheckResult

        results = [CheckResult("Docker installed", "pass", "ok")]

        class FakeChecker:
            def __init__(self, *a, **kw):
                pass

            def run_all(self):
                return results

        monkeypatch.setattr(
            "kamiwaza_extensions.doctor.DoctorChecker",
            FakeChecker,
        )

        runner = CliRunner()
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
