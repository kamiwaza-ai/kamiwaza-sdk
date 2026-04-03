"""Logs command — stream container logs from extension pods."""

from __future__ import annotations

import os
import subprocess
from typing import Optional

import typer
from rich.console import Console

console = Console(stderr=True)

_NAMESPACE = "kamiwaza-extensions"


def run_logs(
    *,
    name: Optional[str] = None,
    service: Optional[str] = None,
    follow: bool = False,
    tail: Optional[int] = None,
) -> None:
    """Stream logs from extension pods."""
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

    # Try to get pod names via status endpoint
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
            # Find target pods
            for svc_status in status.services:
                if service and svc_status.name != service:
                    continue
                for pod in svc_status.pods:
                    if pod.phase in ("Running", "CrashLoopBackOff"):
                        pod_name = pod.name
                        break
                if pod_name:
                    break
        except (APIError, Exception):
            # Status endpoint not available — fall through to label selector
            pass
    finally:
        if old_verify_ssl is None:
            os.environ.pop("KAMIWAZA_VERIFY_SSL", None)
        else:
            os.environ["KAMIWAZA_VERIFY_SSL"] = old_verify_ssl

    # Build kubectl command
    cmd = ["kubectl", "logs", "-n", _NAMESPACE]

    if pod_name:
        cmd.append(pod_name)
    else:
        # Use label selector
        cmd.extend(["-l", f"extensions.kamiwaza.io/deployment-id={dev_name}"])

    if follow:
        cmd.append("--follow")
    if tail is not None:
        cmd.extend(["--tail", str(tail)])

    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")

    try:
        result = subprocess.run(cmd)
        raise typer.Exit(code=result.returncode)
    except FileNotFoundError:
        console.print(
            "[red]Error:[/red] kubectl not found. Install it to view logs."
        )
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        raise typer.Exit(code=0)
