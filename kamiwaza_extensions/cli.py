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

config_app = typer.Typer(help="Configuration commands.")
app.add_typer(config_app, name="config")

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
    # UAC-9d runtime-lib exceptions map to their canonical exit codes
    # (§4.2.7 / §4.2.8). Keep this branch above the generic fallback so
    # the exit code carries meaning for extension authors and CI.
    try:
        from kamiwaza_extensions.exit_codes import exit_code_for
        from kamiwaza_extensions_lib.errors import KamiwazaRuntimeError
    except ImportError:
        KamiwazaRuntimeError = None  # type: ignore[assignment]
        exit_code_for = None  # type: ignore[assignment]

    if KamiwazaRuntimeError is not None and isinstance(exc, KamiwazaRuntimeError):
        console.print(f"[red]Error:[/red] {exc}")
        if _state.debug:
            console.print_exception()
        raise typer.Exit(code=int(exit_code_for(exc.class_name))) from exc

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
    sdk_repo: Optional[str] = typer.Option(
        None,
        "--sdk-repo",
        help="Path to local kamiwaza-sdk checkout — bakes local runtime libs into images",
    ),
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
            sdk_repo=sdk_repo,
        )
    except typer.Exit:
        raise
    except Exception as exc:
        _handle_exception(exc)


@dev_app.command("local")
@run_with_error_handling
def dev_local(
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
    sdk_repo: Optional[str] = typer.Option(
        None,
        "--sdk-repo",
        help="Path to local kamiwaza-sdk checkout for runtime lib override",
    ),
) -> None:
    """Run extension locally with Docker Compose."""
    from kamiwaza_extensions.commands.dev_local import run_dev_local
    run_dev_local(detach=detach, sdk_repo=sdk_repo)


@app.command()
@run_with_error_handling
def status(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Extension name (auto-detected if omitted)"),
) -> None:
    """Show extension deployment status."""
    from kamiwaza_extensions.commands.status import run_status
    run_status(name=name, verbose=_state.verbose)


@app.command()
@run_with_error_handling
def logs(
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Filter to one service"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream continuously"),
    tail: Optional[int] = typer.Option(None, "--tail", help="Number of recent lines to show"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Extension name (auto-detected if omitted)"),
) -> None:
    """Stream logs from a deployed extension."""
    from kamiwaza_extensions.commands.logs import run_logs
    run_logs(service=service, follow=follow, tail=tail, name=name)


@app.command()
@run_with_error_handling
def shell(
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Target service"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Extension name (auto-detected if omitted)"),
) -> None:
    """Exec into a running extension container."""
    from kamiwaza_extensions.commands.shell import run_shell
    run_shell(service=service, name=name)


@app.command("port-forward")
@run_with_error_handling
def port_forward(
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Target service"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Remote container port"),
    local_port: Optional[int] = typer.Option(None, "--local-port", help="Local port (defaults to same as --port)"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Extension name (auto-detected if omitted)"),
) -> None:
    """Forward a local port to an extension pod."""
    from kamiwaza_extensions.commands.port_forward import run_port_forward
    run_port_forward(service=service, port=port, local_port=local_port, name=name)


@app.command()
@run_with_error_handling
def bump(
    level: str = typer.Option("patch", "--level", "-l", help="Bump level: major, minor, or patch"),
) -> None:
    """Bump extension version in kamiwaza.json."""
    from kamiwaza_extensions.commands.bump import run_bump
    run_bump(level=level)


@app.command()
@run_with_error_handling
def convert(
    path: str = typer.Argument(..., help="Path to existing app directory"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without modifying files"),
) -> None:
    """Convert an existing app to a Kamiwaza extension."""
    from kamiwaza_extensions.commands.convert import run_convert
    run_convert(path=path, dry_run=dry_run)


@app.command()
@run_with_error_handling
def publish(
    stage: str = typer.Option(..., "--stage", help="Named publish profile to use"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without making changes"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing version in catalog"),
    no_build: bool = typer.Option(False, "--no-build", help="Skip Docker image build"),
    no_push: bool = typer.Option(False, "--no-push", help="Skip Docker image push"),
) -> None:
    """Publish extension to catalog."""
    from kamiwaza_extensions.commands.publish import run_publish
    run_publish(stage=stage, dry_run=dry_run, force=force, no_build=no_build, no_push=no_push, verbose=_state.verbose)


# ---------------------------------------------------------------------------
# Config subcommands
# ---------------------------------------------------------------------------

@config_app.command("publish-profile")
@run_with_error_handling
def config_publish_profile(
    name: Optional[str] = typer.Argument(None, help="Profile name"),
    registry: Optional[str] = typer.Option(None, "--registry", help="Docker registry URL"),
    catalog_endpoint: Optional[str] = typer.Option(
        None, "--catalog-endpoint", help="S3-compatible catalog endpoint URL"
    ),
    catalog_bucket: Optional[str] = typer.Option(
        None, "--catalog-bucket", help="Bucket name for catalog JSON"
    ),
    catalog_credentials: Optional[str] = typer.Option(
        None, "--catalog-credentials", help="Credential spec (e.g. aws-profile:prod, env, sso)"
    ),
    catalog_prefix: str = typer.Option("", "--catalog-prefix", help="Key prefix within bucket"),
    repo_level: bool = typer.Option(False, "--repo-level", help="Store profile in repo .kz-ext/ dir"),
    list_profiles: bool = typer.Option(False, "--list", "-l", help="List all publish profiles"),
    show: Optional[str] = typer.Option(None, "--show", help="Show details for a profile"),
    delete: Optional[str] = typer.Option(None, "--delete", help="Delete a profile"),
) -> None:
    """Create, list, show, or delete publish profiles."""
    from kamiwaza_extensions.commands.config import publish_profile
    publish_profile(
        name=name,
        registry=registry,
        catalog_endpoint=catalog_endpoint,
        catalog_bucket=catalog_bucket,
        catalog_credentials=catalog_credentials,
        catalog_prefix=catalog_prefix,
        repo_level=repo_level,
        list_profiles=list_profiles,
        show=show,
        delete=delete,
    )
