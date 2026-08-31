"""Dev local command implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import typer
from rich.console import Console

# Errors go to stderr so users can cleanly redirect them
# (`kz-ext dev local --auth 2>errors.log`). Matches the rest of the CLI.
console = Console(stderr=True)

if TYPE_CHECKING:
    from kamiwaza_extensions.extension_detector import ExtensionInfo


def _enforce_cli_contract() -> ExtensionInfo:
    """Return one validated detection or stop on a tooling mismatch."""
    from kamiwaza_extensions.contract_enforcement import enforce_cli_contract
    from kamiwaza_extensions.extension_detector import ExtensionDetector

    info = ExtensionDetector().detect()
    enforce_cli_contract(info.metadata or {}, info.compose_data, console=console)
    return info


def run_dev_local(
    *,
    detach: bool,
    sdk_repo: Optional[str] = None,
    auth: bool = False,
) -> None:
    """Run extension locally with Docker Compose."""
    from kamiwaza_extensions.dev_local import DevLocalRunner
    from kamiwaza_extensions_lib.local_dev import LocalDevAuthError

    info = _enforce_cli_contract()
    runner = DevLocalRunner()
    try:
        exit_code = runner.run(
            detach=detach,
            sdk_repo=sdk_repo,
            auth=auth,
            info=info,
        )
    except LocalDevAuthError as exc:
        # Surface the developer-facing message and exit non-zero so the user
        # sees a clear "run kz-ext login" hint instead of a stack trace.
        console.print(f"[red]--auth bridge unavailable:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    raise typer.Exit(code=exit_code)
