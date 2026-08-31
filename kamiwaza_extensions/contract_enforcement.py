"""Shared fail-fast enforcement for independently versioned CLI contracts."""

from __future__ import annotations

from itertools import chain, repeat
from typing import Iterable, List, Tuple

import typer
from rich.console import Console

from kamiwaza_extensions.exit_codes import ExitCode
from kamiwaza_extensions.extension_detector import ExtensionInfo
from kamiwaza_extensions.validators.metadata import check_cli_contract


def _failures_for_info(info: ExtensionInfo) -> List[Tuple[str, str]]:
    return list(zip(repeat(info.name), check_cli_contract(info.metadata)))


def _format_failure(failure: Tuple[str, str]) -> str:
    name, error = failure
    return f"[red]Error:[/red] {name}: {error}"


def enforce_cli_contracts(
    infos: Iterable[ExtensionInfo], *, console: Console
) -> None:
    """Validate every detected manifest before a lifecycle side effect."""
    failures = list(chain.from_iterable(map(_failures_for_info, infos)))
    if not failures:
        return
    console.print("\n".join(map(_format_failure, failures)))
    raise typer.Exit(code=int(ExitCode.VALIDATION))
