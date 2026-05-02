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
    from kamiwaza_extensions.extension_detector import (
        ExtensionDetector,
        ExtensionNotFoundError,
        MultipleExtensionsError,
    )
    from kamiwaza_extensions.validators.compose import ComposeValidator
    from kamiwaza_extensions.validators.metadata import MetadataValidator
    from kamiwaza_extensions.validators.platform_runtime import PlatformRuntimeValidator

    start_dir = Path(path) if path else Path.cwd()

    try:
        info = ExtensionDetector().detect(start_dir)
    except (ExtensionNotFoundError, MultipleExtensionsError) as exc:
        if json_output:
            typer.echo(json_mod.dumps({"passed": False, "errors": [str(exc)], "warnings": []}))
        else:
            console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    ext_dir = info.path
    metadata_file = ext_dir / "kamiwaza.json"

    # Surface the monorepo rebase so the user knows where validation ran.
    if not json_output and ext_dir.resolve() != start_dir.resolve():
        try:
            rel = ext_dir.resolve().relative_to(start_dir.resolve())
        except ValueError:
            rel = ext_dir
        console.print(
            f"  [yellow]→[/yellow] Detected extension at: [bold]{rel}/[/bold]"
        )

    # Run metadata validation
    meta_validator = MetadataValidator()
    meta_result = meta_validator.validate(metadata_file)

    # Run compose validation
    compose_validator = ComposeValidator()
    runtime_validator = PlatformRuntimeValidator()
    compose_file = _find_compose_file(ext_dir)
    compose_result = None
    runtime_result = None
    if compose_file:
        compose_result = compose_validator.validate(compose_file, ext_dir)
        if compose_result.passed:
            runtime_result = runtime_validator.validate(compose_file, ext_dir)
    else:
        meta_result.warnings.append("No docker-compose file found — kz-ext dev local will not work")

    # Merge results
    all_errors = meta_result.errors[:]
    all_warnings = meta_result.warnings[:]
    if compose_result:
        all_errors.extend(compose_result.errors)
        all_warnings.extend(compose_result.warnings)
    if runtime_result:
        all_errors.extend(runtime_result.errors)
        all_warnings.extend(runtime_result.warnings)

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
