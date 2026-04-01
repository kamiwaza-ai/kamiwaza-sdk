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
    no_verify_ssl: bool = False,
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
        url = "https://kamiwaza.test/api"
        console.print(f"[dim]Using default URL: {url}[/dim]")

    # Normalize URL
    url = url.rstrip("/")

    if no_verify_ssl:
        import os
        os.environ["KAMIWAZA_VERIFY_SSL"] = "false"

    verify_ssl = not no_verify_ssl

    if api_key:
        _login_with_api_key(mgr, url=url, api_key=api_key, name=name, verify_ssl=verify_ssl)
    else:
        _login_with_password(mgr, url=url, name=name, verify_ssl=verify_ssl)


def _login_with_api_key(mgr, *, url: str, api_key: str, name: str, verify_ssl: bool = True) -> None:
    from kamiwaza_sdk.token_store import StoredToken

    # Validate before storing — bad key should not be persisted
    if not _validate_connection(url, api_key, verify_ssl=verify_ssl):
        console.print(
            "[red]Error:[/red] Could not validate connection. "
            "Check the URL and API key are correct."
        )
        raise typer.Exit(code=1)

    token = StoredToken(access_token=api_key, refresh_token=None, expires_at=0.0)
    mgr.add_connection(name=name, url=url, token=token)
    console.print(f"[green]Connected to {url} as '{name}'[/green]")


def _login_with_password(mgr, *, url: str, name: str, verify_ssl: bool = True) -> None:
    from kamiwaza_sdk import KamiwazaClient
    from kamiwaza_sdk.authentication import UserPasswordAuthenticator
    from kamiwaza_sdk.schemas.auth import PATCreate
    from kamiwaza_sdk.token_store import StoredToken, TokenStore

    username = typer.prompt("Username")
    password = typer.prompt("Password", hide_input=True)

    # Use an in-memory token store to avoid reading/writing the shared
    # ~/.kamiwaza/token.json used by the SDK (C1 fix: identity isolation)
    class _MemoryTokenStore(TokenStore):
        def __init__(self):
            self._token = None
        def load(self):
            return self._token
        def save(self, token):
            self._token = token
        def clear(self):
            self._token = None

    # Authenticate via SDK and create a PAT
    client = KamiwazaClient(base_url=url)
    client.authenticator = UserPasswordAuthenticator(
        username, password, client.auth,
        token_store=_MemoryTokenStore(),
    )
    pat_response = client.auth.create_pat(PATCreate(name=f"kz-ext-{name}"))
    pat_token = pat_response.token

    # Store the PAT — auth already succeeded server-side at this point
    token = StoredToken(access_token=pat_token, refresh_token=None, expires_at=0.0)
    mgr.add_connection(name=name, url=url, token=token)
    console.print(f"[green]Connected to {url} as '{name}'[/green]")


def _validate_connection(url: str, token: str, *, verify_ssl: bool = True) -> bool:
    """Check connection by hitting a lightweight endpoint. Returns True if reachable."""
    import requests

    # Try common health endpoints — platform may expose different ones
    for path in ("/auth/ping", "/auth/health", "/health"):
        try:
            resp = requests.get(
                f"{url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
                verify=verify_ssl,
            )
            if resp.ok:
                return True
            # 401/403 means server is up but token is bad
            if resp.status_code in (401, 403):
                return False
        except requests.ConnectionError:
            console.print(
                f"[yellow]Warning:[/yellow] Could not reach {url} — server may be unreachable."
            )
            return False
        except requests.RequestException:
            continue

    # All endpoints returned non-auth errors but server was reachable
    # (e.g. 404 on all health paths) — token validity is unknown
    return True


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
