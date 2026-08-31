"""Dev local command implementation."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

# Errors go to stderr so users can cleanly redirect them
# (`kz-ext dev local --auth 2>errors.log`). Matches the rest of the CLI.
console = Console(stderr=True)


def _enforce_cli_contract() -> None:
    """Stop before local Compose inspection/build on a tooling mismatch."""
    from kamiwaza_extensions.exit_codes import ExitCode
    from kamiwaza_extensions.extension_detector import ExtensionDetector
    from kamiwaza_extensions.validators.metadata import check_cli_contract

    info = ExtensionDetector().detect()
    errors = check_cli_contract(info.metadata or {}, info.compose_data)
    if not errors:
        return
    for error in errors:
        console.print(f"[red]Error:[/red] {error}")
    raise typer.Exit(code=int(ExitCode.VALIDATION))


def run_dev_local(
    *,
    detach: bool,
    sdk_repo: Optional[str] = None,
    auth: bool = False,
) -> None:
    """Run extension locally with Docker Compose."""
    from kamiwaza_extensions.dev_local import DevLocalRunner
    from kamiwaza_extensions_lib.local_dev import LocalDevAuthError

    _enforce_cli_contract()
    runner = DevLocalRunner()
    try:
        exit_code = runner.run(detach=detach, sdk_repo=sdk_repo, auth=auth)
    except LocalDevAuthError as exc:
        # Surface the developer-facing message and exit non-zero so the user
        # sees a clear "run kz-ext login" hint instead of a stack trace.
        console.print(f"[red]--auth bridge unavailable:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    raise typer.Exit(code=exit_code)
