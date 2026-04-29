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

    has_failure = False
    explicit_exit_code: int | None = None
    for result in results:
        if result.status == "pass":
            icon = "[green]✓[/green]"
        elif result.status == "warn":
            icon = "[yellow]![/yellow]"
        else:
            icon = "[red]✗[/red]"
            has_failure = True
            # An explicit `exit_code` (e.g. CLUSTER_NOT_READY=23) must win
            # over the generic FAILURE fallback even when a different,
            # exit-code-less check failed earlier in the run. The previous
            # implementation locked in the first failure's code, which let
            # a generic `fail` shadow a more-specific signal further down
            # (review re-review PR #84 M2).
            if result.exit_code is not None and explicit_exit_code is None:
                explicit_exit_code = result.exit_code

        console.print(f"  {icon} {result.name}: {result.message}")
        if result.fix and result.status != "pass":
            console.print(f"      Fix: {result.fix}", style="dim")

    if has_failure:
        raise typer.Exit(code=explicit_exit_code or int(ExitCode.FAILURE))
