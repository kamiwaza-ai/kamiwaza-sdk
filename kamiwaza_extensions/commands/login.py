"""Login command implementation."""

from __future__ import annotations

import base64
import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# §4.8 B3 fix — warn when the minted PAT's roles are a strict subset of the
# UI role-set. Platform-side fix is tracked separately; this surface catches
# the surprising case where a user with admin roles logs in and is silently
# given a non-admin PAT.
#
# Correctness note: the UI role-set must be captured against the
# *password session* (via client.auth.get_current_user), NOT against the
# just-minted PAT — the PAT only reports its own scoped roles, so a
# self-referential comparison can never show a downgrade.
# ---------------------------------------------------------------------------


_PAT_PAYLOAD_MAX_BYTES = 64 * 1024  # defensive cap on JWT payload size


def _decode_pat_roles(token: Optional[str]) -> set[str]:
    """Extract the ``roles`` claim from a PAT JWT payload.

    Signature is **not** verified — the platform already authenticated
    the caller by the time we reach this code. We only read the payload
    to compare against the UI role-set for B3 diagnostics.

    Defensive against ``None``/non-string inputs (a malformed
    ``create_pat`` response should not crash the login command after
    the connection has been added).
    """
    if not isinstance(token, str):
        return set()
    parts = token.split(".")
    if len(parts) < 2:
        return set()
    payload_b64 = parts[1]
    if len(payload_b64) > _PAT_PAYLOAD_MAX_BYTES:
        return set()
    padding = "=" * (-len(payload_b64) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{payload_b64}{padding}")
        claims = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return set()
    roles = claims.get("roles", [])
    if not isinstance(roles, list):
        return set()
    return {str(r) for r in roles if isinstance(r, str)}


def _capture_ui_roles(client, *, verbose: bool = False) -> set[str]:
    """Best-effort capture of the caller's UI role-set via the SDK's
    already-authenticated password session.

    Must be called *before* PAT mint — otherwise the returned roles
    describe the PAT's scoped subset, defeating the comparison.

    Returns an empty set on any failure. When *verbose* is true, emits
    a dim diagnostic so operators can distinguish "no downgrade
    detected" from "we couldn't tell" (PR review High #3).
    """
    try:
        user = client.auth.get_current_user()
    except Exception as exc:  # noqa: BLE001 — best-effort diagnostic
        if verbose:
            console.print(
                f"[dim]Could not fetch UI role-set for B3 check: {type(exc).__name__}: {exc}[/dim]"
            )
        return set()
    roles = getattr(user, "roles", None) or []
    if not isinstance(roles, list):
        if verbose:
            console.print(
                "[dim]Could not parse UI role-set for B3 check: roles claim was not a list[/dim]"
            )
        return set()
    return {str(r) for r in roles if isinstance(r, str)}


def _warn_if_roles_downgraded(
    *, pat_roles: set[str], ui_roles: set[str]
) -> None:
    """Emit a warning when the UI role-set has roles the PAT is missing.

    Fires on any non-empty ``ui_roles - pat_roles``: catches the strict-
    subset case AND the overlapping-but-disjoint case
    (e.g. ``pat={a,b}``, ``ui={a,c}``). Silent on: equal sets, PAT
    superset, empty UI set (unknown).

    Uses ``typer.echo`` so ``CliRunner`` captures the output through
    Click's normal stdout path.
    """
    if not ui_roles:
        return
    missing = ui_roles - pat_roles
    if not missing:
        return
    typer.echo(
        f"Warning: minted PAT is scoped to {sorted(pat_roles)} but your UI "
        f"role-set also includes {sorted(missing)}. Some admin-gated calls "
        f"may fail; platform-side fix tracked as a follow-on (§4.8 B3)."
    )


def run_login(
    *,
    url: Optional[str],
    api_key: Optional[str],
    name: str,
    list_connections: bool,
    use: Optional[str],
    no_verify_ssl: bool = False,
    verbose: bool = False,
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
        _login_with_password(
            mgr, url=url, name=name, verify_ssl=verify_ssl, verbose=verbose,
        )


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


def _login_with_password(
    mgr, *, url: str, name: str, verify_ssl: bool = True, verbose: bool = False,
) -> None:
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
        # Capture the UI role-set against the password session BEFORE minting
        # the PAT. Post-mint the only credential we have is the scoped PAT,
        # which would reflect only its own roles (§4.8 B3).
        ui_roles = _capture_ui_roles(client, verbose=verbose)
        pat_response = client.auth.create_pat(PATCreate(name=f"kz-ext-{name}"))
        pat_token = pat_response.token
        if not isinstance(pat_token, str) or not pat_token:
            raise RuntimeError(
                "Platform did not return a token for the new PAT. Try again, "
                "or contact the platform team."
            )
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

    # §4.8 B3: surface the role-set downgrade case — silent on failure.
    # `ui_roles` was captured above against the password session.
    _warn_if_roles_downgraded(
        pat_roles=_decode_pat_roles(pat_token),
        ui_roles=ui_roles,
    )


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
