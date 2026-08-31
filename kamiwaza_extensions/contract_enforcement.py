"""Shared fail-fast enforcement for independently versioned CLI contracts."""

from __future__ import annotations

from itertools import chain, repeat
from typing import Any, Dict, Iterable, List, Optional, Tuple

import typer
from rich.console import Console

from kamiwaza_extensions.exit_codes import ExitCode
from kamiwaza_extensions.extension_detector import ExtensionInfo
from kamiwaza_extensions.validators.metadata import check_cli_contract


def _failures_for_info(info: ExtensionInfo) -> List[Tuple[str, str]]:
    return list(
        zip(
            repeat(info.name),
            check_cli_contract(info.metadata, info.compose_data),
        )
    )


def _format_failure(failure: Tuple[Optional[str], str]) -> str:
    name, error = failure
    subject = f"{name}: " if name else ""
    return f"Error: {subject}{error}"


def _abort(failures: Iterable[Tuple[Optional[str], str]], console: Console) -> None:
    """Render untrusted manifest text literally, then exit as validation."""
    console.print(
        "\n".join(map(_format_failure, failures)),
        style="red",
        markup=False,
    )
    raise typer.Exit(code=int(ExitCode.VALIDATION))


def enforce_cli_contract(
    metadata: Dict[str, Any],
    compose_data: Optional[Dict[str, Any]] = None,
    *,
    console: Console,
) -> None:
    """Validate one detected manifest before a lifecycle side effect."""
    errors = check_cli_contract(metadata, compose_data)
    if errors:
        _abort(((None, error) for error in errors), console)


def enforce_cli_contracts(
    infos: Iterable[ExtensionInfo], *, console: Console
) -> None:
    """Validate every detected manifest before a lifecycle side effect."""
    failures = list(chain.from_iterable(map(_failures_for_info, infos)))
    if failures:
        _abort(failures, console)
