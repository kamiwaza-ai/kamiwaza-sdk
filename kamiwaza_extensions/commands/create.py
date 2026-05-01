"""Create / scaffolding command implementation."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()


def run_create(*, type_: str, name: str) -> None:
    """Scaffold a new extension project."""
    from kamiwaza_extensions.scaffolder import Scaffolder, VALID_TYPES

    if type_ not in VALID_TYPES:
        console.print(
            f"[red]Error:[/red] Invalid type '{type_}'. Must be one of: {', '.join(VALID_TYPES)}"
        )
        raise typer.Exit(code=1)

    scaffolder = Scaffolder()
    scaffolder.create(type_=type_, name=name)

    console.print(f"\n[green]✓ Created {type_} extension:[/green] {name}")
    # `--auth` is the right default for app-type extensions: the scaffolded
    # frontend ships with USE_AUTH=true and the local-dev auth bridge wired
    # in, so without `--auth` every protected route 401s and the developer
    # only sees the platform login UI. tool / service shapes don't have a
    # Next.js layer to inject envelope headers, so the bridge doesn't apply.
    # (ENG-3901 dry-run finding F-005.)
    dev_local_cmd = "kz-ext dev local --auth" if type_ == "app" else "kz-ext dev local"
    console.print("\n  Next steps:")
    console.print("    kz-ext validate")
    console.print(f"    {dev_local_cmd}")
    # F-007: the scaffold's docker-compose.yml uses an auto-assigned host
    # port (bare `"3000"` not `"3000:3000"`) to avoid colliding with the
    # platform UI when both run side-by-side. `kz-ext dev local` prints the
    # resolved URL once compose binds (ENG-3901 / F-008); call it out here
    # so first-time developers don't go to localhost:3000 by instinct.
    console.print(
        "[dim]  (the host port is auto-assigned; "
        "kz-ext dev local prints the URL once containers are up)[/dim]"
    )
