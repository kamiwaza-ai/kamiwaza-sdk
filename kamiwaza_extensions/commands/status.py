"""Status command — show extension deployment status."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console(stderr=True)


def run_status(*, name: Optional[str] = None, verbose: bool = False) -> None:
    """Display extension deployment status."""
    from kamiwaza_sdk import KamiwazaClient
    from kamiwaza_sdk.exceptions import APIError

    from kamiwaza_extensions.connections import ConnectionManager
    from kamiwaza_extensions.constants import extract_user_id

    # Resolve connection + auth
    conn_mgr = ConnectionManager()
    connection = conn_mgr.get_active_connection()
    if connection is None:
        console.print(
            "[red]Error:[/red] No Kamiwaza connection. Run: [bold]kz-ext login <url>[/bold]"
        )
        raise typer.Exit(code=1)

    token = conn_mgr.get_token()
    if token is None:
        console.print(
            "[red]Error:[/red] Token expired. Run: [bold]kz-ext login[/bold]"
        )
        raise typer.Exit(code=1)

    # Resolve extension name
    if name is None:
        from kamiwaza_extensions.extension_detector import ExtensionDetector
        from kamiwaza_extensions.payload_builder import PayloadBuilder

        detector = ExtensionDetector()
        info = detector.detect()
        dev_name = PayloadBuilder.make_dev_name(
            info.name, user_id=extract_user_id(token.access_token)
        )
    else:
        dev_name = name

    from kamiwaza_extensions.constants import ssl_env_override
    with ssl_env_override(connection):
        client = KamiwazaClient(
            base_url=connection.url, api_key=token.access_token
        )

        try:
            status = client.extensions.get_extension_status(dev_name)
        except APIError as exc:
            if exc.status_code == 404:
                console.print(
                    f"[red]Error:[/red] Extension '{dev_name}' not found."
                )
                console.print(
                    "  Run: [bold]kz-ext dev[/bold] to deploy first."
                )
                raise typer.Exit(code=1) from exc
            raise

        # Display header
        console.print(f"Extension:  [bold]{status.name}[/bold]")
        console.print(f"Phase:      {status.phase}")
        if status.url:
            console.print(f"URL:        [blue]{status.url}[/blue]")
        console.print()

        # Services table
        svc_table = Table(title="Services")
        svc_table.add_column("NAME", style="bold")
        svc_table.add_column("IMAGE TAG")
        svc_table.add_column("READY")
        svc_table.add_column("RESTARTS")

        for svc in status.services:
            ready_str = f"{svc.ready_replicas}/{svc.replicas}"
            svc_table.add_row(
                svc.name, svc.image_tag, ready_str, str(svc.restart_count)
            )

        console.print(svc_table)

        # Events
        if status.events:
            console.print()
            evt_table = Table(title="Recent Events")
            evt_table.add_column("TYPE")
            evt_table.add_column("REASON")
            evt_table.add_column("MESSAGE")
            evt_table.add_column("COUNT")

            for evt in status.events:
                style = "yellow" if evt.type == "Warning" else None
                evt_table.add_row(
                    evt.type,
                    evt.reason,
                    evt.message,
                    str(evt.count),
                    style=style,
                )

            console.print(evt_table)
