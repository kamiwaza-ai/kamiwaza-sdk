"""Tests for DoctorChecker."""

import json
from unittest.mock import patch, MagicMock

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
            mock_run.return_value = MagicMock(returncode=0, stdout="Docker version 24.0.0, build abc123")
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
            mock_run.return_value = MagicMock(returncode=0, stdout="Docker Compose v2.24.0")
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
        # Use a range that fits the current compat bundle window (>=0.2,<0.4
        # at time of writing). PR-86 H7/M6 made this check range-vs-range
        # accurate — `>=0.1.0` would now correctly warn as below the floor.
        req_file.write_text("kamiwaza-extensions-lib>=0.3.0,<0.4\nfastapi\n")
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
        pkg_file.write_text(json.dumps({
            "dependencies": {"@kamiwaza-ai/extensions-lib": "^0.4.0"}
        }))
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
                "Cluster extension readiness", "fail",
                "CRD missing", fix="reinstall", exit_code=23,
            ),
        ]

        class FakeChecker:
            def __init__(self, *a, **kw):
                pass

            def run_all(self):
                return results

        monkeypatch.setattr(
            "kamiwaza_extensions.doctor.DoctorChecker", FakeChecker,
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
            "kamiwaza_extensions.doctor.DoctorChecker", FakeChecker,
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
            "kamiwaza_extensions.doctor.DoctorChecker", FakeChecker,
        )

        runner = CliRunner()
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
