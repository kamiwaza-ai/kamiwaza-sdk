"""Runtime-neutral JSON-file command adapter for scenario providers."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from kamiwaza_sdk.validation.models import (
    FixtureState,
    RuntimeContext,
    ScenarioCatalog,
    ScenarioPlan,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import (
    ProviderContractError,
    ScenarioProvider,
    validate_fixture_state_snapshots,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


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
    except (json.JSONDecodeError, OSError):
        print("protocol input/output failed", file=sys.stderr)
        return 2
    return 0


def _execute(provider: ScenarioProvider, args: argparse.Namespace) -> None:
    if args.command == "describe":
        catalog = ScenarioCatalog(tuple(provider.describe()))
        payload = catalog.model_dump(mode="json", by_alias=True)
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return
    if args.command == "resolve":
        profile = _read_model(args.profile, ValidationProfile)
        _write_model(args.plan, provider.resolve(profile))
        return
    runtime = _read_model(args.runtime, RuntimeContext)
    if args.command == "prepare":
        plan = _read_model(args.plan, ScenarioPlan)
        writer = _FixtureStateFileWriter(args.state)
        state = provider.prepare(plan, runtime, writer)
        validate_fixture_state_snapshots(writer.snapshots, state)
        return
    state = _read_model(args.state, FixtureState)
    if args.command == "run":
        plan = _read_model(args.plan, ScenarioPlan)
        _write_model(args.evidence, provider.run(plan, runtime, state))
        return
    _write_model(args.evidence, provider.teardown(runtime, state))


def _read_model(path: Path, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def _write_model(path: Path, model: BaseModel, *, private: bool = False) -> None:
    mode = 0o600 if private else 0o644
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    os.fchmod(descriptor, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(
            model.model_dump(mode="json", by_alias=True),
            stream,
            sort_keys=True,
            separators=(",", ":"),
        )
        stream.write("\n")
    os.replace(temporary, path)


def _validation_fields(error: ValidationError) -> str:
    locations = {
        ".".join(str(part) for part in item["loc"])
        for item in error.errors(include_url=False, include_input=False)
    }
    return ", ".join(sorted(locations)) or "unknown field"


class _FixtureStateFileWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.snapshots: list[FixtureState] = []

    def write(self, state: FixtureState) -> None:
        _write_model(self.path, state, private=True)
        self.snapshots.append(state)


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
