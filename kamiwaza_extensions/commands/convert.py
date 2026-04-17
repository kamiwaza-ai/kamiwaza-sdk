"""Convert command — analyze and convert existing apps to Kamiwaza extensions."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

console = Console(stderr=True)


def run_convert(
    *,
    path: str,
    dry_run: bool = False,
) -> None:
    """Analyze and convert an existing app to a Kamiwaza extension."""
    from kamiwaza_extensions.app_analyzer import AppAnalyzer
    from kamiwaza_extensions.convert_agent import run_agent

    app_dir = Path(path).resolve()

    console.print(f"Converting [bold]{app_dir.name}[/bold]...\n")

    # 1. Analyze the app
    console.print("  Analyzing...")
    analyzer = AppAnalyzer()
    try:
        analysis = analyzer.analyze(app_dir)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    # Print analysis summary
    console.print(f"    Type:              [bold]{analysis.extension_type}[/bold]")
    mode_label = "generic AI fallback" if analysis.conversion_mode == "generic" else "structured"
    console.print(f"    Mode:              [bold]{mode_label}[/bold]")
    if analysis.services:
        svc_names = ", ".join(s.name for s in analysis.services)
        console.print(f"    Services:          {svc_names}")
    else:
        console.print("    Services:          [yellow]none detected[/yellow]")

    if analysis.compose_path:
        console.print(f"    Compose:           [green]\u2713[/green] {analysis.compose_path.name}")
    else:
        console.print("    Compose:           [red]\u2717[/red] not found")

    # Compatibility
    if analysis.has_host_ports:
        console.print(f"    Host ports:        [yellow]\u26a0[/yellow] {', '.join(analysis.has_host_ports)}")
    if analysis.has_bind_mounts:
        console.print(f"    Bind mounts:       [yellow]\u26a0[/yellow] {len(analysis.has_bind_mounts)} found")
    if analysis.missing_resource_limits:
        console.print(
            f"    Resource limits:   [red]\u2717[/red] missing on: {', '.join(analysis.missing_resource_limits)}"
        )
    if analysis.has_health_endpoint:
        console.print("    Health endpoint:   [green]\u2713[/green] detected")
    else:
        console.print("    Health endpoint:   [red]\u2717[/red] not detected")

    # SDK integration
    if analysis.has_python_runtime_lib:
        console.print("    Python runtime:    [green]\u2713[/green] kamiwaza-extensions-lib found")
    else:
        console.print("    Python runtime:    [red]\u2717[/red] not found")
    if analysis.has_ts_runtime_lib:
        console.print("    TS runtime:        [green]\u2713[/green] @kamiwaza-ai/extensions-lib found")
    else:
        has_frontend = any(s.language == "node" for s in analysis.services)
        if has_frontend:
            console.print("    TS runtime:        [red]\u2717[/red] not found")

    console.print()

    # 2. Run AI agent for file modifications
    console.print()
    plan = run_agent(analysis, dry_run=dry_run)

    # 3. Print results
    if plan.success and plan.modifications:
        console.print()
        if dry_run:
            console.print("  [bold]Would modify:[/bold]")
        else:
            console.print("  [bold]Files modified:[/bold]")

        for mod in plan.modifications:
            if mod.action == "create":
                icon = "[green]\u2713[/green]" if not dry_run else "[dim]\u2713[/dim]"
                action = "created" if not dry_run else "would create"
            else:
                icon = "[green]\u2713[/green]" if not dry_run else "[dim]\u2713[/dim]"
                action = "modified" if not dry_run else "would modify"
            desc = f" ({mod.description})" if mod.description else ""
            console.print(f"    {icon} {mod.path} — {action}{desc}")

    if plan.errors:
        console.print()
        console.print("  [bold red]Conversion failed:[/bold red]")
        for err in plan.errors:
            console.print(f"    [red]\u2717[/red] {err}")

    if plan.manual_items:
        console.print()
        console.print("  [bold yellow]Manual attention needed:[/bold yellow]")
        for item in plan.manual_items:
            console.print(f"    [yellow]\u26a0[/yellow] {item}")

    if plan.summary:
        console.print()
        console.print(f"  [dim]{plan.summary}[/dim]")

    if not plan.success:
        raise typer.Exit(code=1)

    # 4. Next steps
    console.print()
    console.print("  [bold]Next steps:[/bold]")
    console.print("    git diff                    # Review all changes")
    console.print("    kz-ext validate             # Verify extension requirements")
    console.print("    kz-ext dev local            # Test locally")
