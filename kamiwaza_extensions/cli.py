"""CLI entry point for kz-ext."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

from kamiwaza_extensions import __version__

app = typer.Typer(
    name="kz-ext",
    help="Kamiwaza extension developer tools.",
)

dev_app = typer.Typer(help="Development commands (local and remote deploy).")
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


@dev_app.callback(invoke_without_command=True)
def dev_callback(
    ctx: typer.Context,
    no_build: bool = typer.Option(False, "--no-build", help="Skip image build"),
    no_push: bool = typer.Option(False, "--no-push", help="Skip registry push"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Build/push one service only"),
    revision: Optional[str] = typer.Option(None, "--revision", "-r", help="Custom revision tag"),
) -> None:
    """Build, push, and deploy extension to Kamiwaza cluster.

    With no subcommand, runs the full remote deploy pipeline.
    Use 'kz-ext dev local' for local Docker Compose development.
    """
    if ctx.invoked_subcommand is not None:
        return
    # No subcommand → run remote deploy
    from kamiwaza_extensions.commands.dev import run_dev_remote
    try:
        run_dev_remote(
            no_build=no_build,
            no_push=no_push,
            service=service,
            revision=revision,
            verbose=_state.verbose,
        )
    except typer.Exit:
        raise
    except Exception as exc:
        _handle_exception(exc)


@app.command()
@run_with_error_handling
def status(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Extension name (default: auto-detect)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show extra detail"),
) -> None:
    """Show extension deployment status."""
    from kamiwaza_extensions.commands.status import run_status
    run_status(name=name, verbose=verbose)


@app.command()
@run_with_error_handling
def logs(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Extension name (default: auto-detect)"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Target a specific service"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    tail: Optional[int] = typer.Option(None, "--tail", "-t", help="Number of recent lines to show"),
) -> None:
    """Stream logs from extension pods."""
    from kamiwaza_extensions.commands.logs import run_logs
    run_logs(name=name, service=service, follow=follow, tail=tail)


@app.command()
@run_with_error_handling
def shell(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Extension name (default: auto-detect)"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Target a specific service's pod"),
) -> None:
    """Open an interactive shell in an extension pod."""
    from kamiwaza_extensions.commands.shell import run_shell
    run_shell(name=name, service=service)


@dev_app.command("local")
@run_with_error_handling
def dev_local(
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
    use_auth: bool = typer.Option(
        False,
        "--auth",
        help="Run with KAMIWAZA_USE_AUTH=true instead of local anonymous mode",
    ),
    use_auth_bridge: bool = typer.Option(
        True,
        "--auth-bridge/--no-auth-bridge",
        help=(
            "When --auth is enabled, reuse the active kz-ext connection to bridge "
            "real Kamiwaza identity into localhost requests"
        ),
    ),
) -> None:
    """Run extension locally with Docker Compose."""
    from kamiwaza_extensions.commands.dev_local import run_dev_local
    run_dev_local(
        detach=detach,
        use_auth=use_auth,
        use_auth_bridge=use_auth_bridge,
    )
