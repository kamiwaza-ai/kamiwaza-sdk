"""Shell command — open an interactive shell in an extension pod."""

from __future__ import annotations

import os
from typing import Optional

import typer
from rich.console import Console

console = Console(stderr=True)

_NAMESPACE = "kamiwaza-extensions"


def run_shell(
    *,
    name: Optional[str] = None,
    service: Optional[str] = None,
) -> None:
    """Open an interactive shell in an extension pod."""
    from kamiwaza_sdk import KamiwazaClient
    from kamiwaza_sdk.exceptions import APIError

    from kamiwaza_extensions.connections import ConnectionManager
    from kamiwaza_extensions.commands.dev import _extract_user_id

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
            info.name, user_id=_extract_user_id(token.access_token)
        )
    else:
        dev_name = name

    # Get pod name via status endpoint
    pod_name = None
    old_verify_ssl = os.environ.get("KAMIWAZA_VERIFY_SSL")
    if not connection.verify_ssl:
        os.environ["KAMIWAZA_VERIFY_SSL"] = "false"
    try:
        client = KamiwazaClient(
            base_url=connection.url, api_key=token.access_token
        )
        try:
            status = client.extensions.get_extension_status(dev_name)
            # Find target pod: prefer --service, else primary/first running pod
            for svc_status in status.services:
                if service and svc_status.name != service:
                    continue
                for pod in svc_status.pods:
                    if pod.phase == "Running":
                        pod_name = pod.name
                        break
                if pod_name:
                    break
        except APIError as exc:
            if exc.status_code == 404:
                console.print(
                    f"[red]Error:[/red] Extension '{dev_name}' not found."
                )
                console.print(
                    "  Run: [bold]kz-ext dev[/bold] to deploy first."
                )
                raise typer.Exit(code=1) from exc
            # Other errors: try to proceed without pod name
            console.print(
                "[yellow]Warning:[/yellow] Could not fetch extension status. "
                "Trying label selector fallback."
            )
        except Exception:
            console.print(
                "[yellow]Warning:[/yellow] Could not fetch extension status."
            )
    finally:
        if old_verify_ssl is None:
            os.environ.pop("KAMIWAZA_VERIFY_SSL", None)
        else:
            os.environ["KAMIWAZA_VERIFY_SSL"] = old_verify_ssl

    if pod_name is None:
        console.print(
            "[red]Error:[/red] No running pod found"
            + (f" for service '{service}'" if service else "")
            + "."
        )
        console.print("  Check status with: [bold]kz-ext status[/bold]")
        raise typer.Exit(code=1)

    cmd = [
        "kubectl", "exec", "-it",
        "-n", _NAMESPACE,
        pod_name,
        "--", "/bin/sh",
    ]
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")

    # Replace the current process with kubectl exec
    try:
        os.execvp("kubectl", cmd)
    except FileNotFoundError:
        console.print(
            "[red]Error:[/red] kubectl not found. Install it to open a shell."
        )
        raise typer.Exit(code=1)
