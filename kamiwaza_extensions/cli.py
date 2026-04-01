"""CLI entry point for kz-ext."""

from __future__ import annotations

import sys
import traceback
from typing import Optional

import typer
from rich.console import Console

from kamiwaza_extensions import __version__

app = typer.Typer(
    name="kz-ext",
    help="Kamiwaza extension developer tools.",
)

dev_app = typer.Typer(help="Local development commands.")
app.add_typer(dev_app, name="dev")

console = Console(stderr=True)

# ---------------------------------------------------------------------------
# Global state passed via typer context
# ---------------------------------------------------------------------------

class _GlobalState:
    verbose: bool = False
    debug: bool = False

_state = _GlobalState()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    debug: bool = typer.Option(False, "--debug", help="Show debug output including tracebacks"),
    version: bool = typer.Option(False, "--version", help="Print version and exit"),
) -> None:
    """Kamiwaza extension developer tools."""
    if version:
        typer.echo(f"kz-ext {__version__}")
        raise typer.Exit()
    _state.verbose = verbose
    _state.debug = debug
    if ctx.invoked_subcommand is None and not version:
        typer.echo(ctx.get_help())
        raise typer.Exit()


def get_state() -> _GlobalState:
    return _state


# ---------------------------------------------------------------------------
# Error handling wrapper
# ---------------------------------------------------------------------------

def run_with_error_handling(func):
    """Decorator that catches exceptions and formats them for CLI output."""
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except typer.Exit:
            raise
        except typer.Abort:
            raise
        except FileNotFoundError as exc:
            console.print(f"[red]Error:[/red] File not found: {exc}")
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            _handle_exception(exc)

    return wrapper


def _handle_exception(exc: Exception) -> None:
    # Try to import Kamiwaza errors for nicer formatting
    try:
        from kamiwaza_sdk.exceptions import KamiwazaError
    except ImportError:
        KamiwazaError = None

    if KamiwazaError is not None and isinstance(exc, KamiwazaError):
        console.print(f"[red]Error:[/red] {exc}")
        if _state.debug:
            console.print_exception()
        raise typer.Exit(code=1) from exc

    console.print(f"[red]Error:[/red] {exc}")
    if _state.debug:
        console.print_exception()
    raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

@app.command()
@run_with_error_handling
def login(
    url: Optional[str] = typer.Argument(None, help="Kamiwaza instance URL [default: https://kamiwaza.test/api]"),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="Authenticate with an existing PAT/API key"),
    name: str = typer.Option("default", "--name", "-n", help="Connection name"),
    list_connections: bool = typer.Option(False, "--list", "-l", help="List stored connections"),
    use: Optional[str] = typer.Option(None, "--use", help="Switch active connection"),
    no_verify_ssl: bool = typer.Option(False, "--no-verify-ssl", help="Disable SSL certificate verification"),
    dev: bool = typer.Option(False, "--dev", help="Shorthand for local dev: uses https://kamiwaza.test/api with SSL verification off"),
) -> None:
    """Authenticate with a Kamiwaza instance."""
    from kamiwaza_extensions.commands.login import run_login
    if dev:
        url = url or "https://kamiwaza.test/api"
        no_verify_ssl = True
    run_login(url=url, api_key=api_key, name=name, list_connections=list_connections, use=use, no_verify_ssl=no_verify_ssl)


@app.command()
@run_with_error_handling
def validate(
    path: Optional[str] = typer.Argument(None, help="Path to extension directory"),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON"),
) -> None:
    """Validate extension metadata and compose files."""
    from kamiwaza_extensions.commands.validate import run_validate
    run_validate(path=path, json_output=json_output)


@app.command()
@run_with_error_handling
def doctor() -> None:
    """Check development environment health."""
    from kamiwaza_extensions.commands.doctor import run_doctor
    run_doctor()


@app.command()
@run_with_error_handling
def create(
    type_: str = typer.Option(..., "--type", "-t", help="Extension type: app, tool, or service"),
    name: str = typer.Option(..., "--name", "-n", help="Extension name"),
) -> None:
    """Scaffold a new extension project."""
    from kamiwaza_extensions.commands.create import run_create
    run_create(type_=type_, name=name)


@dev_app.command("local")
@run_with_error_handling
def dev_local(
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
) -> None:
    """Run extension locally with Docker Compose."""
    from kamiwaza_extensions.commands.dev_local import run_dev_local
    run_dev_local(detach=detach)
