"""Bump command — increment extension version in kamiwaza.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

console = Console(stderr=True)


def run_bump(*, level: str = "patch") -> None:
    """Bump the version in kamiwaza.json."""
    from packaging.version import Version

    from kamiwaza_extensions.extension_detector import ExtensionDetector

    detector = ExtensionDetector()
    info = detector.detect()

    kamiwaza_json_path = info.path / "kamiwaza.json"
    if not kamiwaza_json_path.exists():
        console.print("[red]Error:[/red] kamiwaza.json not found.")
        raise typer.Exit(code=1)

    # Parse current version
    try:
        current = Version(info.version)
    except Exception:
        console.print(
            f"[red]Error:[/red] Current version '{info.version}' is not valid semver."
        )
        raise typer.Exit(code=1)

    # Compute new version
    major, minor, patch = current.major, current.minor, current.micro
    if level == "major":
        new_version = f"{major + 1}.0.0"
    elif level == "minor":
        new_version = f"{major}.{minor + 1}.0"
    elif level == "patch":
        new_version = f"{major}.{minor}.{patch + 1}"
    else:
        console.print(
            f"[red]Error:[/red] Unknown level '{level}'. Use: major, minor, patch"
        )
        raise typer.Exit(code=1)

    # Update kamiwaza.json
    with kamiwaza_json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    old_version = data.get("version", info.version)
    data["version"] = new_version

    with kamiwaza_json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
        f.write("\n")

    console.print(
        f"[green]\u2713[/green] Bumped version: "
        f"[bold]{old_version}[/bold] → [bold]{new_version}[/bold] ({level})"
    )
