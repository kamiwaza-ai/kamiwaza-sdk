"""Doctor command implementation."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()


def run_doctor() -> None:
    """Check development environment health."""
    from kamiwaza_extensions.doctor import DoctorChecker

    checker = DoctorChecker()
    results = checker.run_all()

    has_failure = False
    for result in results:
        if result.status == "pass":
            icon = "[green]✓[/green]"
        elif result.status == "warn":
            icon = "[yellow]![/yellow]"
        else:
            icon = "[red]✗[/red]"
            has_failure = True

        console.print(f"  {icon} {result.name}: {result.message}")
        if result.fix and result.status != "pass":
            console.print(f"      Fix: {result.fix}", style="dim")

    if has_failure:
        raise typer.Exit(code=1)
