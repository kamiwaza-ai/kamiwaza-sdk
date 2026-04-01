"""Validate command implementation."""

from __future__ import annotations

import json as json_mod
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

console = Console()


def run_validate(*, path: Optional[str], json_output: bool) -> None:
    """Validate extension metadata and compose files."""
    from kamiwaza_extensions.validators.metadata import MetadataValidator
    from kamiwaza_extensions.validators.compose import ComposeValidator

    ext_dir = Path(path) if path else Path.cwd()

    metadata_file = ext_dir / "kamiwaza.json"
    if not metadata_file.exists():
        console.print(f"[red]Error:[/red] No kamiwaza.json found in {ext_dir}")
        raise typer.Exit(code=1)

    # Run metadata validation
    meta_validator = MetadataValidator()
    meta_result = meta_validator.validate(metadata_file)

    # Run compose validation
    compose_validator = ComposeValidator()
    compose_file = _find_compose_file(ext_dir)
    compose_result = None
    if compose_file:
        compose_result = compose_validator.validate(compose_file, ext_dir)

    # Merge results
    all_errors = meta_result.errors[:]
    all_warnings = meta_result.warnings[:]
    if compose_result:
        all_errors.extend(compose_result.errors)
        all_warnings.extend(compose_result.warnings)

    passed = len(all_errors) == 0

    if json_output:
        output = {
            "passed": passed,
            "errors": all_errors,
            "warnings": all_warnings,
        }
        typer.echo(json_mod.dumps(output, indent=2))
    else:
        if all_errors:
            for err in all_errors:
                console.print(f"  [red]✗[/red] {err}")
        if all_warnings:
            for warn in all_warnings:
                console.print(f"  [yellow]![/yellow] {warn}")
        if passed:
            console.print("[green]✓ Validation passed[/green]", highlight=False)
            if all_warnings:
                console.print(f"  ({len(all_warnings)} warning(s))")
        else:
            console.print(
                f"[red]✗ Validation failed:[/red] {len(all_errors)} error(s), {len(all_warnings)} warning(s)"
            )

    if not passed:
        raise typer.Exit(code=1)


def _find_compose_file(ext_dir: Path) -> Optional[Path]:
    from kamiwaza_extensions.constants import COMPOSE_FILENAMES

    for name in COMPOSE_FILENAMES:
        candidate = ext_dir / name
        if candidate.exists():
            return candidate
    return None
