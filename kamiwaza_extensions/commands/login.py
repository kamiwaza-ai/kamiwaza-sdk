"""Login command implementation."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def run_login(
    *,
    url: Optional[str],
    api_key: Optional[str],
    name: str,
    list_connections: bool,
    use: Optional[str],
) -> None:
    """Authenticate with a Kamiwaza instance."""
    from kamiwaza_extensions.connections import ConnectionManager

    mgr = ConnectionManager()

    if list_connections:
        _show_connections(mgr)
        return

    if use is not None:
        mgr.set_active(use)
        console.print(f"[green]Switched to connection:[/green] {use}")
        return

    if url is None:
        console.print("[red]Error:[/red] URL is required. Usage: kz-ext login <url>")
        raise typer.Exit(code=1)

    # Normalize URL
    url = url.rstrip("/")

    if api_key:
        _login_with_api_key(mgr, url=url, api_key=api_key, name=name)
    else:
        _login_with_password(mgr, url=url, name=name)


def _login_with_api_key(mgr, *, url: str, api_key: str, name: str) -> None:
    from kamiwaza_sdk.token_store import StoredToken

    # Validate the connection works
    _validate_connection(url, api_key)

    token = StoredToken(access_token=api_key, refresh_token=None, expires_at=0.0)
    mgr.add_connection(name=name, url=url, token=token)
    console.print(f"[green]Connected to {url} as '{name}'[/green]")


def _login_with_password(mgr, *, url: str, name: str) -> None:
    from kamiwaza_sdk import KamiwazaClient
    from kamiwaza_sdk.authentication import UserPasswordAuthenticator
    from kamiwaza_sdk.schemas.auth import PATCreate
    from kamiwaza_sdk.token_store import StoredToken

    username = typer.prompt("Username")
    password = typer.prompt("Password", hide_input=True)

    # Authenticate via SDK and create a PAT
    client = KamiwazaClient(base_url=url)
    client.authenticator = UserPasswordAuthenticator(username, password, client.auth)
    pat_response = client.auth.create_pat(PATCreate(name=f"kz-ext-{name}"))
    pat_token = pat_response.token

    # Validate the PAT works
    _validate_connection(url, pat_token)

    token = StoredToken(access_token=pat_token, refresh_token=None, expires_at=0.0)
    mgr.add_connection(name=name, url=url, token=token)
    console.print(f"[green]Connected to {url} as '{name}'[/green]")


def _validate_connection(url: str, token: str) -> None:
    """Check connection by hitting a lightweight endpoint."""
    import requests

    try:
        resp = requests.get(
            f"{url}/api/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        console.print(
            f"[yellow]Warning:[/yellow] Could not validate connection to {url}: {exc}\n"
            "  Credentials were stored, but the server may be unreachable."
        )


def _show_connections(mgr) -> None:
    connections = mgr.list_connections()
    if not connections:
        console.print("No stored connections. Run [bold]kz-ext login <url>[/bold] to add one.")
        return

    table = Table(title="Stored Connections")
    table.add_column("Active", style="green", width=6)
    table.add_column("Name", style="cyan")
    table.add_column("URL")

    for conn in connections:
        active = "  *" if conn.active else ""
        table.add_row(active, conn.name, conn.url)

    console.print(table)
