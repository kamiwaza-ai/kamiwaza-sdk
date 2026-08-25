"""Runtime-neutral JSON-file command adapter for scenario providers."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from kamiwaza_sdk.validation.models import (
    CleanupEvidence,
    FixtureState,
    RuntimeContext,
    ScenarioCatalog,
    ScenarioEvidence,
    ScenarioPlan,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import (
    ProviderContractError,
    ScenarioProvider,
    require_passed,
    validate_cleanup_identity,
    validate_descriptor_registry,
    validate_evidence_identity,
    validate_fixture_state_transition,
    validate_fixture_state_snapshots,
    validate_plan_completeness,
    validate_plan_identity,
    validate_plan_registry,
    validate_plan_runtime_identity,
    validate_provider_output,
    validate_state_identity,
    validate_state_runtime_identity,
)
from kamiwaza_sdk.validation.registry import evaluate_coverage

ModelT = TypeVar("ModelT", bound=BaseModel)
CallbackT = TypeVar("CallbackT")


class _AdapterContractError(ProviderContractError):
    """Trusted, fixed adapter diagnostic raised through a provider callback."""


def provider_main(provider: ScenarioProvider, argv: Sequence[str] | None = None) -> int:
    """Execute one provider phase and return a process-compatible exit code."""

    args = _parser().parse_args(argv)
    try:
        _execute(provider, args)
    except ValidationError as error:
        fields = _validation_fields(error)
        print(f"protocol input validation failed: {fields}", file=sys.stderr)
        return 2
    except ProviderContractError as error:
        print(f"provider contract failed: {error}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        print("protocol input/output failed", file=sys.stderr)
        return 2
    except Exception:
        print("provider execution failed", file=sys.stderr)
        return 2
    return 0


def _execute(provider: ScenarioProvider, args: argparse.Namespace) -> None:
    if args.command == "describe":
        catalog = validate_provider_output(
            _provider_callback(lambda: tuple(provider.describe())), ScenarioCatalog
        )
        validate_descriptor_registry(catalog.root)
        payload = catalog.model_dump(mode="json", by_alias=True)
        print(
            json.dumps(
                payload,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return
    if args.command == "resolve":
        profile = _read_model(args.profile, ValidationProfile)
        catalog = validate_provider_output(
            _provider_callback(lambda: tuple(provider.describe())), ScenarioCatalog
        )
        validate_descriptor_registry(catalog.root)
        plan = validate_provider_output(
            _provider_callback(lambda: provider.resolve(profile)), ScenarioPlan
        )
        validate_plan_registry(catalog.root, plan)
        validate_plan_identity(profile, plan)
        validate_plan_completeness(profile, catalog.root, plan)
        _write_model(args.plan, plan)
        return
    runtime = _read_model(args.runtime, RuntimeContext)
    if args.command == "prepare":
        plan = _read_model(args.plan, ScenarioPlan)
        validate_plan_runtime_identity(plan, runtime)
        writer = _FixtureStateFileWriter(args.state, plan, runtime)
        state = validate_provider_output(
            _provider_callback(lambda: provider.prepare(plan, runtime, writer)),
            FixtureState,
        )
        writer.require_valid()
        validate_fixture_state_snapshots(writer.snapshots, state)
        validate_state_identity(plan, runtime, state)
        return
    state = _read_model(args.state, FixtureState)
    if args.command == "run":
        plan = _read_model(args.plan, ScenarioPlan)
        validate_plan_runtime_identity(plan, runtime)
        validate_state_identity(plan, runtime, state)
        evidence = validate_provider_output(
            _provider_callback(lambda: provider.run(plan, runtime, state)),
            ScenarioEvidence,
        )
        validate_evidence_identity(plan, state, evidence)
        _write_model(args.evidence, evidence)
        coverage = evaluate_coverage(plan, evidence)
        require_passed(coverage.status, "provider evidence failed exact coverage")
        return
    validate_state_runtime_identity(runtime, state)
    cleanup = validate_provider_output(
        _provider_callback(lambda: provider.teardown(runtime, state)), CleanupEvidence
    )
    validate_cleanup_identity(runtime, state, cleanup)
    _write_model(args.evidence, cleanup)
    require_passed(cleanup.status, "provider semantic cleanup failed")


def _read_model(path: Path, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def _provider_callback(callback: Callable[[], CallbackT]) -> CallbackT:
    try:
        return callback()
    except _AdapterContractError:
        raise
    except ProviderContractError:
        raise ProviderContractError("provider callback failed") from None


def _write_model(path: Path, model: BaseModel, *, private: bool = False) -> None:
    mode = 0o600 if private else 0o644
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, mode)
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with stream:
            json.dump(
                model.model_dump(mode="json", by_alias=True),
                stream,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validation_fields(error: ValidationError) -> str:
    locations = {
        ".".join(str(part) for part in item["loc"])
        for item in error.errors(include_url=False, include_input=False)
    }
    return ", ".join(sorted(locations)) or "unknown field"


class _FixtureStateFileWriter:
    def __init__(self, path: Path, plan: ScenarioPlan, runtime: RuntimeContext) -> None:
        self.path = path
        self.plan = plan
        self.runtime = runtime
        self.snapshots: list[FixtureState] = []
        self.violation: _AdapterContractError | None = None

    def write(self, state: FixtureState) -> None:
        if self.violation is not None:
            raise self.violation
        try:
            validated = validate_provider_output(state, FixtureState)
            validate_state_identity(self.plan, self.runtime, validated)
            previous = self.snapshots[-1] if self.snapshots else None
            validate_fixture_state_transition(previous, validated)
        except ProviderContractError as error:
            self.violation = _AdapterContractError(str(error))
            raise self.violation from None
        try:
            _write_model(self.path, validated, private=True)
        except OSError:
            self.violation = _AdapterContractError(
                "fixture state persistence failed"
            )
            raise self.violation from None
        self.snapshots.append(validated)

    def require_valid(self) -> None:
        if self.violation is not None:
            raise self.violation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scenario-provider")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("describe").add_argument(
        "--json", action="store_true", required=True
    )
    resolve = commands.add_parser("resolve")
    _path_argument(resolve, "--profile")
    _path_argument(resolve, "--plan")
    prepare = commands.add_parser("prepare")
    _path_argument(prepare, "--plan")
    _path_argument(prepare, "--runtime")
    _path_argument(prepare, "--state")
    run = commands.add_parser("run")
    _path_argument(run, "--plan")
    _path_argument(run, "--runtime")
    _path_argument(run, "--state")
    _path_argument(run, "--evidence")
    teardown = commands.add_parser("teardown")
    _path_argument(teardown, "--runtime")
    _path_argument(teardown, "--state")
    _path_argument(teardown, "--evidence")
    return parser


def _path_argument(parser: argparse.ArgumentParser, name: str) -> None:
    parser.add_argument(name, required=True, type=Path)
