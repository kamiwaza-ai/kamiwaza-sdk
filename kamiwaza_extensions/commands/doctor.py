"""Doctor command implementation."""

from __future__ import annotations

import typer
from rich.console import Console

from kamiwaza_extensions.exit_codes import ExitCode

console = Console()


def run_doctor() -> None:
    """Check development environment health."""
    from kamiwaza_extensions.doctor import DoctorChecker

    checker = DoctorChecker()
    results = checker.run_all()

    fail_exit_code: int | None = None
    for result in results:
        if result.status == "pass":
            icon = "[green]✓[/green]"
        elif result.status == "warn":
            icon = "[yellow]![/yellow]"
        else:
            icon = "[red]✗[/red]"
            # First failing CheckResult with an explicit exit_code wins; a
            # generic failure falls back to ExitCode.FAILURE.
            if fail_exit_code is None:
                fail_exit_code = result.exit_code or int(ExitCode.FAILURE)

        console.print(f"  {icon} {result.name}: {result.message}")
        if result.fix and result.status != "pass":
            console.print(f"      Fix: {result.fix}", style="dim")

    if fail_exit_code is not None:
        raise typer.Exit(code=fail_exit_code)
