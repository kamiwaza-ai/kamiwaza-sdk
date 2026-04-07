"""Config commands — manage publish profiles and other settings."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console(stderr=True)


def publish_profile(
    *,
    name: Optional[str] = None,
    registry: Optional[str] = None,
    catalog_endpoint: Optional[str] = None,
    catalog_bucket: Optional[str] = None,
    catalog_credentials: Optional[str] = None,
    catalog_prefix: str = "",
    repo_level: bool = False,
    list_profiles: bool = False,
    show: Optional[str] = None,
    delete: Optional[str] = None,
) -> None:
    """Create, list, show, or delete publish profiles."""
    from kamiwaza_extensions.profile_manager import ProfileManager, PublishProfile

    mgr = ProfileManager()

    # --list mode
    if list_profiles:
        _list_profiles(mgr)
        return

    # --show mode
    if show is not None:
        _show_profile(mgr, show)
        return

    # --delete mode
    if delete is not None:
        _delete_profile(mgr, delete, repo_level=repo_level)
        return

    # Create/update mode — name and all required fields must be present
    if name is None:
        console.print(
            "[red]Error:[/red] Profile name is required when creating a profile."
        )
        raise typer.Exit(code=1)

    missing = []
    if not registry:
        missing.append("--registry")
    if not catalog_endpoint:
        missing.append("--catalog-endpoint")
    if not catalog_bucket:
        missing.append("--catalog-bucket")
    if not catalog_credentials:
        missing.append("--catalog-credentials")

    if missing:
        console.print(
            f"[red]Error:[/red] Missing required options: {', '.join(missing)}"
        )
        raise typer.Exit(code=1)

    profile = PublishProfile(
        name=name,
        registry=registry,  # type: ignore[arg-type]
        catalog_endpoint=catalog_endpoint,  # type: ignore[arg-type]
        catalog_bucket=catalog_bucket,  # type: ignore[arg-type]
        catalog_credentials=catalog_credentials,  # type: ignore[arg-type]
        catalog_prefix=catalog_prefix,
    )

    extension_dir = None
    if repo_level:
        # Use the extension root (where kamiwaza.json lives), not cwd
        try:
            from kamiwaza_extensions.extension_detector import ExtensionDetector
            extension_dir = ExtensionDetector().detect().path
        except Exception:
            from pathlib import Path
            extension_dir = Path.cwd()

    saved_path = mgr.save_profile(profile, repo_level=repo_level, extension_dir=extension_dir)

    console.print(f"[green]\u2713[/green] Publish profile [bold]'{name}'[/bold] saved to {saved_path}")
    console.print()
    console.print(f"  Registry:           {registry}")
    console.print(f"  Catalog endpoint:   {catalog_endpoint}")
    console.print(f"  Catalog bucket:     {catalog_bucket}")
    console.print(f"  Credentials:        {catalog_credentials}")
    if catalog_prefix:
        console.print(f"  Catalog prefix:     {catalog_prefix}")


def _list_profiles(mgr) -> None:
    """List all profiles as a formatted table."""
    from pathlib import Path

    profiles_with_source = mgr.list_profiles_with_source(extension_dir=Path.cwd())

    if not profiles_with_source:
        console.print("No publish profiles found.")
        console.print(
            "  Run [bold]kz-ext config publish-profile <name> --registry ... --catalog-endpoint ... "
            "--catalog-bucket ... --catalog-credentials ...[/bold] to create one."
        )
        return

    console.print("[bold]Publish Profiles:[/bold]")
    console.print()

    table = Table(show_header=True, box=None, pad_edge=False, padding=(0, 2))
    table.add_column("NAME", style="bold")
    table.add_column("REGISTRY")
    table.add_column("BUCKET")
    table.add_column("CREDENTIALS")
    table.add_column("SOURCE")

    for profile, source in sorted(profiles_with_source, key=lambda ps: ps[0].name):
        table.add_row(
            profile.name,
            profile.registry,
            profile.catalog_bucket,
            profile.catalog_credentials,
            source,
        )

    console.print(table)


def _show_profile(mgr, name: str) -> None:
    """Show details of one profile."""
    from pathlib import Path

    try:
        profile = mgr.get_profile(name, extension_dir=Path.cwd())
    except ValueError:
        console.print(f"[red]Error:[/red] Profile '{name}' not found.")
        raise typer.Exit(code=1)

    console.print(f"[bold]Profile:[/bold] {profile.name}")
    console.print()
    console.print(f"  Registry:           {profile.registry}")
    console.print(f"  Catalog endpoint:   {profile.catalog_endpoint}")
    console.print(f"  Catalog bucket:     {profile.catalog_bucket}")
    console.print(f"  Credentials:        {profile.catalog_credentials}")
    if profile.catalog_prefix:
        console.print(f"  Catalog prefix:     {profile.catalog_prefix}")
    if profile.created_at:
        console.print(f"  Created at:         {profile.created_at}")


def _delete_profile(mgr, name: str, *, repo_level: bool = False) -> None:
    """Delete a profile by name."""
    from pathlib import Path

    extension_dir = Path.cwd()

    try:
        mgr.delete_profile(name, repo_level=repo_level, extension_dir=extension_dir)
    except ValueError:
        console.print(f"[red]Error:[/red] Profile '{name}' not found.")
        raise typer.Exit(code=1)

    scope = "repo-level" if repo_level else "user-level"
    console.print(f"[green]\u2713[/green] Deleted {scope} profile [bold]'{name}'[/bold].")
