"""JSON-on-disk command contract for scenario providers."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from kamiwaza_sdk.validation import (
    CleanupEvidence,
    FixtureState,
    RuntimeContext,
    ScenarioEvidence,
    ScenarioPlan,
)
from kamiwaza_sdk.validation.cli import _fsync_directory, provider_main
from kamiwaza_sdk.validation.golden_provider import GoldenProvider
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import RecordingFixtureStateWriter

from .support import profile_payload

pytestmark = pytest.mark.contract


@dataclass(frozen=True)
class EvidenceFailure:
    command: str
    provider: GoldenProvider
    expected_error: str


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


def _assert_provider_failure(
    provider: GoldenProvider,
    args: list[str],
    capsys: pytest.CaptureFixture[str],
    artifact: tuple[Path, str],
) -> None:
    output_path, expected_error = artifact
    assert provider_main(provider, args) == 2
    assert expected_error in capsys.readouterr().err
    assert not output_path.exists()


def _assert_failed_with_evidence(
    provider: GoldenProvider,
    args: list[str],
    capsys: pytest.CaptureFixture[str],
    artifact: tuple[Path, str],
) -> None:
    output_path, expected_error = artifact
    assert provider_main(provider, args) == 2
    assert expected_error in capsys.readouterr().err
    assert output_path.exists()


def _write_golden_profile(tmp_path: Path) -> Path:
    profile_path = tmp_path / "profile.json"
    payload = profile_payload()
    payload["validation"]["include"] = ["sdk.golden.echo/v1"]  # type: ignore[index]
    _write_json(profile_path, payload)
    return profile_path


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


class WrongResolveDigestProvider(GoldenProvider):
    def resolve(self, profile):  # type: ignore[no-untyped-def]
        return (
            super()
            .resolve(profile)
            .model_copy(update={"profile_digest": "sha256:" + "0" * 64})
        )


class ForeignResolveTargetProvider(GoldenProvider):
    def resolve(self, profile):  # type: ignore[no-untyped-def]
        plan = super().resolve(profile)
        selected = plan.selected[0].model_copy(update={"target_id": "foreign-target"})
        return plan.model_copy(update={"selected": (selected,)})


class UndescribedResolveCaseProvider(GoldenProvider):
    def resolve(self, profile):  # type: ignore[no-untyped-def]
        plan = super().resolve(profile)
        selected = plan.selected[0].model_copy(update={"case_ids": ("other",)})
        return plan.model_copy(update={"selected": (selected,)})


class ExplodingResolveProvider(GoldenProvider):
    def resolve(self, profile):  # type: ignore[no-untyped-def]
        raise RuntimeError("sensitive-provider-value")


class WrongRunOutputProvider(GoldenProvider):
    def run(self, plan, runtime, state):  # type: ignore[no-untyped-def]
        return state


class WrongRunDigestProvider(GoldenProvider):
    def run(self, plan, runtime, state):  # type: ignore[no-untyped-def]
        return (
            super()
            .run(plan, runtime, state)
            .model_copy(update={"plan_digest": "sha256:" + "0" * 64})
        )


class WrongRunStateDigestProvider(GoldenProvider):
    def run(self, plan, runtime, state):  # type: ignore[no-untyped-def]
        return (
            super()
            .run(plan, runtime, state)
            .model_copy(update={"state_digest": "sha256:" + "0" * 64})
        )


class IncompleteRunEvidenceProvider(GoldenProvider):
    def run(self, plan, runtime, state):  # type: ignore[no-untyped-def]
        return super().run(plan, runtime, state).model_copy(update={"results": ()})


class WrongTeardownOutputProvider(GoldenProvider):
    def teardown(self, runtime, state):  # type: ignore[no-untyped-def]
        return state


class EmptyCleanupProvider(GoldenProvider):
    def teardown(self, runtime, state):  # type: ignore[no-untyped-def]
        return super().teardown(runtime, state).model_copy(update={"results": ()})


class WrongCleanupDigestProvider(GoldenProvider):
    def teardown(self, runtime, state):  # type: ignore[no-untyped-def]
        return (
            super()
            .teardown(runtime, state)
            .model_copy(update={"state_digest": "sha256:" + "0" * 64})
        )


class FailedCleanupProvider(GoldenProvider):
    def teardown(self, runtime, state):  # type: ignore[no-untyped-def]
        cleanup = super().teardown(runtime, state)
        failed = cleanup.results[0].model_copy(update={"status": "failed"})
        return cleanup.model_copy(update={"status": "failed", "results": (failed,)})


class WrongSnapshotOutputProvider(GoldenProvider):
    def prepare(self, plan, runtime, state_writer):  # type: ignore[no-untyped-def]
        state_writer.write(plan)
        return super().prepare(plan, runtime, state_writer)


class RegressiveSnapshotProvider(GoldenProvider):
    def prepare(self, plan, runtime, state_writer):  # type: ignore[no-untyped-def]
        state = super().prepare(plan, runtime, RecordingFixtureStateWriter())
        initial = state.model_copy(update={"journal": ()})
        for snapshot in (initial, state, initial, state):
            state_writer.write(snapshot)
        return state


class InvalidPartialIdentityProvider(GoldenProvider):
    def prepare(self, plan, runtime, state_writer):  # type: ignore[no-untyped-def]
        state = super().prepare(plan, runtime, RecordingFixtureStateWriter())
        state_writer.write(state.model_copy(update={"run_id": "foreign-run"}))
        raise ProviderContractError("simulated invalid partial state")


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


@pytest.mark.parametrize(
    ("provider", "expected_error"),
    [
        (WrongResolveOutputProvider(), "provider contract failed"),
        (WrongResolveDigestProvider(), "plan profile digest mismatch"),
        (ForeignResolveTargetProvider(), "undeclared target"),
        (UndescribedResolveCaseProvider(), "undescribed case"),
    ],
)
def test_cli_rejects_invalid_or_detached_plans(
    provider: GoldenProvider,
    expected_error: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = _write_golden_profile(tmp_path)
    plan_path = tmp_path / "plan.json"
    _assert_provider_failure(
        provider,
        ["resolve", "--profile", str(profile_path), "--plan", str(plan_path)],
        capsys,
        (plan_path, expected_error),
    )


@pytest.mark.parametrize(
    "case",
    [
        EvidenceFailure("run", WrongRunOutputProvider(), "provider contract failed"),
        EvidenceFailure(
            "teardown", WrongTeardownOutputProvider(), "provider contract failed"
        ),
        EvidenceFailure(
            "teardown",
            EmptyCleanupProvider(),
            "cleanup resource inventory mismatch",
        ),
        EvidenceFailure(
            "run", WrongRunDigestProvider(), "evidence plan digest mismatch"
        ),
        EvidenceFailure(
            "run", WrongRunStateDigestProvider(), "evidence state digest mismatch"
        ),
        EvidenceFailure(
            "teardown",
            WrongCleanupDigestProvider(),
            "cleanup state digest mismatch",
        ),
    ],
)
def test_cli_rejects_invalid_or_detached_evidence(
    case: EvidenceFailure,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path, runtime_path, state_path = _write_prepared_inputs(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    args = [case.command, "--runtime", str(runtime_path), "--state", str(state_path)]
    if case.command == "run":
        args.extend(["--plan", str(plan_path)])
    args.extend(["--evidence", str(evidence_path)])

    _assert_provider_failure(
        case.provider, args, capsys, (evidence_path, case.expected_error)
    )


def test_cli_rejects_state_detached_from_runtime_before_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path, runtime_path, state_path = _write_prepared_inputs(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    state = FixtureState.model_validate_json(state_path.read_text())
    state_path.write_text(
        state.model_copy(update={"run_id": "foreign-run"}).model_dump_json(
            by_alias=True
        ),
        encoding="utf-8",
    )

    _assert_provider_failure(
        GoldenProvider(),
        [
            "run",
            "--plan",
            str(plan_path),
            "--runtime",
            str(runtime_path),
            "--state",
            str(state_path),
            "--evidence",
            str(evidence_path),
        ],
        capsys,
        (evidence_path, "fixture state run identity mismatch"),
    )


def test_cli_rejects_runtime_content_changed_after_prepare(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path, runtime_path, state_path = _write_prepared_inputs(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    runtime = RuntimeContext.model_validate_json(runtime_path.read_text())
    changed_cluster = runtime.clusters[0].model_copy(
        update={
            "base_url": "https://other.example.test/api",
            "api_key_ref": "secret://other/admin-pat",
        }
    )
    runtime_path.write_text(
        runtime.model_copy(update={"clusters": (changed_cluster,)}).model_dump_json(
            by_alias=True
        ),
        encoding="utf-8",
    )

    _assert_provider_failure(
        GoldenProvider(),
        [
            "run",
            "--plan",
            str(plan_path),
            "--runtime",
            str(runtime_path),
            "--state",
            str(state_path),
            "--evidence",
            str(evidence_path),
        ],
        capsys,
        (evidence_path, "fixture state runtime digest mismatch"),
    )


def test_cli_rejects_state_detached_from_plan_before_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path, runtime_path, state_path = _write_prepared_inputs(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    plan = ScenarioPlan.model_validate_json(plan_path.read_text())
    plan_path.write_text(
        plan.model_copy(
            update={"install_requirements": {"tampered": True}}
        ).model_dump_json(by_alias=True),
        encoding="utf-8",
    )

    _assert_provider_failure(
        GoldenProvider(),
        [
            "run",
            "--plan",
            str(plan_path),
            "--runtime",
            str(runtime_path),
            "--state",
            str(state_path),
            "--evidence",
            str(evidence_path),
        ],
        capsys,
        (evidence_path, "fixture state plan digest mismatch"),
    )


def test_cli_invalid_utf8_input_fails_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile_path = tmp_path / "profile.json"
    plan_path = tmp_path / "plan.json"
    profile_path.write_bytes(b"\xff")

    _assert_provider_failure(
        GoldenProvider(),
        ["resolve", "--profile", str(profile_path), "--plan", str(plan_path)],
        capsys,
        (plan_path, "protocol input/output failed"),
    )


def test_cli_rejects_wrong_fixture_snapshot_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path, runtime_path, state_path = _write_prepare_inputs(
        tmp_path, GoldenProvider()
    )

    _assert_provider_failure(
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
        capsys,
        (state_path, "provider contract failed"),
    )


def test_cli_rejects_regressive_fixture_snapshots_but_retains_final_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = RegressiveSnapshotProvider()
    plan_path, runtime_path, state_path = _write_prepare_inputs(tmp_path, provider)

    assert (
        provider_main(
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
        == 2
    )
    assert "fixture journal snapshot regressed" in capsys.readouterr().err
    assert state_path.exists()


def test_cli_refuses_invalid_partial_state_before_persisting_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = InvalidPartialIdentityProvider()
    plan_path, runtime_path, state_path = _write_prepare_inputs(tmp_path, provider)

    assert (
        provider_main(
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
        == 2
    )
    assert "fixture state run identity mismatch" in capsys.readouterr().err
    assert not state_path.exists()


def test_cli_semantic_run_failure_returns_nonzero_with_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path, runtime_path, state_path = _write_prepared_inputs(tmp_path)
    evidence_path = tmp_path / "evidence.json"

    _assert_failed_with_evidence(
        IncompleteRunEvidenceProvider(),
        [
            "run",
            "--plan",
            str(plan_path),
            "--runtime",
            str(runtime_path),
            "--state",
            str(state_path),
            "--evidence",
            str(evidence_path),
        ],
        capsys,
        (evidence_path, "provider evidence failed exact coverage"),
    )
    assert ScenarioEvidence.model_validate_json(evidence_path.read_text()).results == ()


def test_cli_failed_cleanup_returns_nonzero_with_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, runtime_path, state_path = _write_prepared_inputs(tmp_path)
    evidence_path = tmp_path / "cleanup.json"

    _assert_failed_with_evidence(
        FailedCleanupProvider(),
        [
            "teardown",
            "--runtime",
            str(runtime_path),
            "--state",
            str(state_path),
            "--evidence",
            str(evidence_path),
        ],
        capsys,
        (evidence_path, "provider semantic cleanup failed"),
    )
    assert (
        CleanupEvidence.model_validate_json(evidence_path.read_text()).status
        == "failed"
    )


def test_cli_sanitizes_unexpected_provider_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile_path = _write_golden_profile(tmp_path)
    plan_path = tmp_path / "plan.json"

    assert (
        provider_main(
            ExplodingResolveProvider(),
            ["resolve", "--profile", str(profile_path), "--plan", str(plan_path)],
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "provider execution failed" in captured.err
    assert "sensitive-provider-value" not in captured.err
    assert not plan_path.exists()


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


def test_file_writes_do_not_require_posix_fchmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = GoldenProvider()
    plan_path, runtime_path, state_path = _write_prepare_inputs(tmp_path, provider)
    monkeypatch.delattr(os, "fchmod")

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


def test_directory_sync_is_skipped_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(
        os,
        "open",
        lambda *_args, **_kwargs: pytest.fail("Windows must not open directories"),
    )

    _fsync_directory(tmp_path)


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
