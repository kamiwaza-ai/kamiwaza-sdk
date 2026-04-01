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

    verify_ssl = not no_verify_ssl

    if api_key:
        _login_with_api_key(mgr, url=url, api_key=api_key, name=name, verify_ssl=verify_ssl)
    else:
        _login_with_password(mgr, url=url, name=name, verify_ssl=verify_ssl)


def _login_with_api_key(mgr, *, url: str, api_key: str, name: str, verify_ssl: bool = True) -> None:
    from kamiwaza_sdk.token_store import StoredToken

    # Validate the key works against an auth-required endpoint
    if not _validate_token(url, api_key, verify_ssl=verify_ssl):
        console.print(
            "[red]Error:[/red] Could not validate connection. "
            "Check the URL and API key are correct."
        )
        raise typer.Exit(code=1)

    token = StoredToken(access_token=api_key, refresh_token=None, expires_at=0.0)
    mgr.add_connection(name=name, url=url, token=token, verify_ssl=verify_ssl)
    console.print(f"[green]Connected to {url} as '{name}'[/green]")


def _login_with_password(mgr, *, url: str, name: str, verify_ssl: bool = True) -> None:
    from kamiwaza_sdk import KamiwazaClient
    from kamiwaza_sdk.authentication import UserPasswordAuthenticator
    from kamiwaza_sdk.schemas.auth import PATCreate
    from kamiwaza_sdk.token_store import StoredToken, TokenStore

    username = typer.prompt("Username")
    password = typer.prompt("Password", hide_input=True)

    # Use an in-memory token store to avoid reading/writing the shared
    # ~/.kamiwaza/token.json used by the SDK
    class _MemoryTokenStore(TokenStore):
        def __init__(self):
            self._token = None
        def load(self):
            return self._token
        def save(self, token):
            self._token = token
        def clear(self):
            self._token = None

    # Pass verify_ssl explicitly to the SDK client via env var scoped
    # to this operation only
    import os
    old_verify = os.environ.get("KAMIWAZA_VERIFY_SSL")
    if not verify_ssl:
        os.environ["KAMIWAZA_VERIFY_SSL"] = "false"

    try:
        client = KamiwazaClient(base_url=url)
        client.authenticator = UserPasswordAuthenticator(
            username, password, client.auth,
            token_store=_MemoryTokenStore(),
        )
        pat_response = client.auth.create_pat(PATCreate(name=f"kz-ext-{name}"))
        pat_token = pat_response.token
    except Exception as exc:
        console.print(f"[red]Error:[/red] Authentication failed: {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        # Restore env var
        if old_verify is None:
            os.environ.pop("KAMIWAZA_VERIFY_SSL", None)
        else:
            os.environ["KAMIWAZA_VERIFY_SSL"] = old_verify

    # Store the PAT — auth already succeeded server-side at this point
    token = StoredToken(access_token=pat_token, refresh_token=None, expires_at=0.0)
    mgr.add_connection(name=name, url=url, token=token, verify_ssl=verify_ssl)
    console.print(f"[green]Connected to {url} as '{name}'[/green]")


def _validate_token(url: str, token: str, *, verify_ssl: bool = True) -> bool:
    """Validate a token by calling an auth-required endpoint. Returns True if valid."""
    import requests

    # Use /auth/pats (list PATs) — requires valid auth, not publicly accessible
    try:
        resp = requests.get(
            f"{url}/auth/pats",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
            verify=verify_ssl,
        )
        if resp.ok:
            return True
        if resp.status_code in (401, 403):
            return False
        # Server error (500, 502, etc.) — can't confirm token validity
        console.print(
            f"[yellow]Warning:[/yellow] Server returned {resp.status_code} during validation. "
            "Token was not verified — storing credentials anyway."
        )
        return True
    except requests.ConnectionError:
        console.print(
            f"[yellow]Warning:[/yellow] Could not reach {url} — server may be unreachable."
        )
        return False
    except requests.RequestException as exc:
        console.print(
            f"[yellow]Warning:[/yellow] Validation request failed: {exc}. "
            "Token was not verified — storing credentials anyway."
        )
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
