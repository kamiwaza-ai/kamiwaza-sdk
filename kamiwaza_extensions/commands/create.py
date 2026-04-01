"""Create / scaffolding command implementation."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()

VALID_TYPES = ("app", "tool", "service")


def run_create(*, type_: str, name: str) -> None:
    """Scaffold a new extension project."""
    from kamiwaza_extensions.scaffolder import Scaffolder

    if type_ not in VALID_TYPES:
        console.print(f"[red]Error:[/red] Invalid type '{type_}'. Must be one of: {', '.join(VALID_TYPES)}")
        raise typer.Exit(code=1)

    scaffolder = Scaffolder()
    scaffolder.create(type_=type_, name=name)

    console.print(f"\n[green]✓ Created {type_} extension:[/green] {name}/")
    console.print(f"\n  Next steps:")
    console.print(f"    cd {name}")
    console.print(f"    kz-ext validate")
    console.print(f"    kz-ext dev local")
