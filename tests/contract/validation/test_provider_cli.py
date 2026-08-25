"""JSON-on-disk command contract for scenario providers."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from kamiwaza_sdk.validation import (
    CleanupEvidence,
    FixtureState,
    RuntimeContext,
    ScenarioEvidence,
    ScenarioPlan,
)
from kamiwaza_sdk.validation.cli import provider_main
from kamiwaza_sdk.validation.golden_provider import GoldenProvider
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import RecordingFixtureStateWriter

from .support import profile_payload

pytestmark = pytest.mark.contract


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _runtime_payload() -> dict[str, object]:
    return {
        "schema": "kamiwaza.runtime-context/v1",
        "run_id": "run-123",
        "clusters": [
            {
                "id": "evo-x2-2",
                "base_url": "https://evo-x2-2.example.test/api",
                "api_key_ref": "secret://evo-x2-2/admin-pat",
                "kubeconfig_ref": "file:///run/secrets/evo-x2-2.kubeconfig",
            }
        ],
    }


def _assert_provider_command(provider: GoldenProvider, *args: str) -> None:
    assert provider_main(provider, list(args)) == 0


def _write_prepare_inputs(
    tmp_path: Path, provider: GoldenProvider
) -> tuple[Path, Path, Path]:
    profile_path = tmp_path / "profile.json"
    plan_path = tmp_path / "plan.json"
    runtime_path = tmp_path / "runtime.json"
    state_path = tmp_path / "state.json"
    payload = profile_payload()
    payload["validation"]["include"] = ["sdk.golden.echo/v1"]  # type: ignore[index]
    _write_json(profile_path, payload)
    _write_json(runtime_path, _runtime_payload())
    _assert_provider_command(
        provider,
        "resolve",
        "--profile",
        str(profile_path),
        "--plan",
        str(plan_path),
    )
    return plan_path, runtime_path, state_path


def _write_prepared_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    provider = GoldenProvider()
    plan_path, runtime_path, state_path = _write_prepare_inputs(tmp_path, provider)
    _assert_provider_command(
        provider,
        "prepare",
        "--plan",
        str(plan_path),
        "--runtime",
        str(runtime_path),
        "--state",
        str(state_path),
    )
    return plan_path, runtime_path, state_path


def test_golden_provider_cli_runs_the_full_json_file_lifecycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile_path = tmp_path / "profile.json"
    plan_path = tmp_path / "plan.json"
    runtime_path = tmp_path / "runtime.json"
    state_path = tmp_path / "state.json"
    evidence_path = tmp_path / "evidence.json"
    cleanup_path = tmp_path / "cleanup.json"
    payload = profile_payload()
    payload["validation"]["include"] = ["sdk.golden.echo/v1"]  # type: ignore[index]
    _write_json(profile_path, payload)
    _write_json(runtime_path, _runtime_payload())
    provider = GoldenProvider()

    _assert_provider_command(provider, "describe", "--json")
    described = json.loads(capsys.readouterr().out)
    assert described[0]["scenario_id"] == "sdk.golden.echo/v1"

    _assert_provider_command(
        provider, "resolve", "--profile", str(profile_path), "--plan", str(plan_path)
    )
    _assert_provider_command(
        provider,
        "prepare",
        "--plan",
        str(plan_path),
        "--runtime",
        str(runtime_path),
        "--state",
        str(state_path),
    )
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    _assert_provider_command(
        provider,
        "run",
        "--plan",
        str(plan_path),
        "--runtime",
        str(runtime_path),
        "--state",
        str(state_path),
        "--evidence",
        str(evidence_path),
    )
    _assert_provider_command(
        provider,
        "teardown",
        "--runtime",
        str(runtime_path),
        "--state",
        str(state_path),
        "--evidence",
        str(cleanup_path),
    )

    ScenarioPlan.model_validate_json(plan_path.read_text())
    RuntimeContext.model_validate_json(runtime_path.read_text())
    FixtureState.model_validate_json(state_path.read_text())
    ScenarioEvidence.model_validate_json(evidence_path.read_text())
    cleanup = CleanupEvidence.model_validate_json(cleanup_path.read_text())
    assert cleanup.status == "passed"


def test_cli_invalid_input_fails_without_leaking_values_or_writing_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile_path = tmp_path / "profile.json"
    plan_path = tmp_path / "plan.json"
    payload = profile_payload()
    payload["api_key"] = "sensitive-token-value"
    _write_json(profile_path, payload)

    exit_code = provider_main(
        GoldenProvider(),
        ["resolve", "--profile", str(profile_path), "--plan", str(plan_path)],
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert "protocol input validation failed" in captured.err
    assert "api_key" in captured.err
    assert "sensitive-token-value" not in captured.err
    assert not plan_path.exists()


def test_golden_provider_module_is_an_executable_provider_command() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "kamiwaza_sdk.validation.golden_provider",
            "describe",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    described = json.loads(completed.stdout)
    assert described[0]["scenario_id"] == "sdk.golden.echo/v1"


def test_describe_requires_explicit_json_output() -> None:
    with pytest.raises(SystemExit) as error:
        provider_main(GoldenProvider(), ["describe"])

    assert error.value.code == 2


class PartialPrepareFailureProvider(GoldenProvider):
    def prepare(self, plan, runtime, state_writer):  # type: ignore[no-untyped-def]
        class FailAfterFirstMutation:
            def write(self, state: FixtureState) -> None:
                state_writer.write(state)
                if state.journal:
                    raise ProviderContractError("simulated partial prepare failure")

        return super().prepare(plan, runtime, FailAfterFirstMutation())


class NonJournalingProvider(GoldenProvider):
    def prepare(self, plan, runtime, state_writer):  # type: ignore[no-untyped-def]
        return super().prepare(plan, runtime, RecordingFixtureStateWriter())


class WrongResolveOutputProvider(GoldenProvider):
    def resolve(self, profile):  # type: ignore[no-untyped-def]
        return profile


class WrongRunOutputProvider(GoldenProvider):
    def run(self, plan, runtime, state):  # type: ignore[no-untyped-def]
        return state


class WrongTeardownOutputProvider(GoldenProvider):
    def teardown(self, runtime, state):  # type: ignore[no-untyped-def]
        return state


class WrongSnapshotOutputProvider(GoldenProvider):
    def prepare(self, plan, runtime, state_writer):  # type: ignore[no-untyped-def]
        state_writer.write(plan)
        return super().prepare(plan, runtime, state_writer)


def test_cli_preserves_private_partial_state_when_prepare_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = PartialPrepareFailureProvider()
    plan_path, runtime_path, state_path = _write_prepare_inputs(tmp_path, provider)

    exit_code = provider_main(
        provider,
        [
            "prepare",
            "--plan",
            str(plan_path),
            "--runtime",
            str(runtime_path),
            "--state",
            str(state_path),
        ],
    )

    assert exit_code == 2
    assert capsys.readouterr().out == ""
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    partial_state = FixtureState.model_validate_json(state_path.read_text())
    assert len(partial_state.journal) == 1


def test_cli_rejects_provider_that_does_not_persist_prepare_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = NonJournalingProvider()
    plan_path, runtime_path, state_path = _write_prepare_inputs(tmp_path, provider)

    exit_code = provider_main(
        provider,
        [
            "prepare",
            "--plan",
            str(plan_path),
            "--runtime",
            str(runtime_path),
            "--state",
            str(state_path),
        ],
    )

    assert exit_code == 2
    assert "did not persist fixture state" in capsys.readouterr().err
    assert not state_path.exists()


def test_cli_rejects_wrong_resolve_output_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile_path = tmp_path / "profile.json"
    plan_path = tmp_path / "plan.json"
    _write_json(profile_path, profile_payload())

    exit_code = provider_main(
        WrongResolveOutputProvider(),
        ["resolve", "--profile", str(profile_path), "--plan", str(plan_path)],
    )

    assert exit_code == 2
    assert "provider contract failed" in capsys.readouterr().err
    assert not plan_path.exists()


@pytest.mark.parametrize(
    ("command", "provider"),
    [
        ("run", WrongRunOutputProvider()),
        ("teardown", WrongTeardownOutputProvider()),
    ],
)
def test_cli_rejects_wrong_evidence_output_models(
    command: str,
    provider: GoldenProvider,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path, runtime_path, state_path = _write_prepared_inputs(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    args = [command, "--runtime", str(runtime_path), "--state", str(state_path)]
    if command == "run":
        args.extend(["--plan", str(plan_path)])
    args.extend(["--evidence", str(evidence_path)])

    exit_code = provider_main(provider, args)

    assert exit_code == 2
    assert "provider contract failed" in capsys.readouterr().err
    assert not evidence_path.exists()


def test_cli_rejects_wrong_fixture_snapshot_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path, runtime_path, state_path = _write_prepare_inputs(
        tmp_path, GoldenProvider()
    )

    exit_code = provider_main(
        WrongSnapshotOutputProvider(),
        [
            "prepare",
            "--plan",
            str(plan_path),
            "--runtime",
            str(runtime_path),
            "--state",
            str(state_path),
        ],
    )

    assert exit_code == 2
    assert "provider contract failed" in capsys.readouterr().err
    assert not state_path.exists()


def test_fixture_state_write_fsyncs_file_and_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = GoldenProvider()
    plan_path, runtime_path, state_path = _write_prepare_inputs(tmp_path, provider)
    fsync_targets: list[str] = []

    def record_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        fsync_targets.append("directory" if stat.S_ISDIR(mode) else "file")

    monkeypatch.setattr(os, "fsync", record_fsync)

    _assert_provider_command(
        provider,
        "prepare",
        "--plan",
        str(plan_path),
        "--runtime",
        str(runtime_path),
        "--state",
        str(state_path),
    )

    assert fsync_targets == ["file", "directory", "file", "directory"]


def test_fixture_state_write_does_not_reuse_another_attempts_temporary_file(
    tmp_path: Path,
) -> None:
    provider = GoldenProvider()
    plan_path, runtime_path, state_path = _write_prepare_inputs(tmp_path, provider)
    stale_temporary = state_path.with_name(f".{state_path.name}.tmp")
    stale_temporary.write_text("another writer", encoding="utf-8")

    _assert_provider_command(
        provider,
        "prepare",
        "--plan",
        str(plan_path),
        "--runtime",
        str(runtime_path),
        "--state",
        str(state_path),
    )

    assert stale_temporary.read_text(encoding="utf-8") == "another writer"
