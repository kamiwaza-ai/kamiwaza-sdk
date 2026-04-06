"""Port-forward command — forward a local port to an extension pod."""

from __future__ import annotations

import os
from typing import Optional

import typer
from rich.console import Console

console = Console(stderr=True)

# Common ports to try when no port info is available from the status response.
_COMMON_PORTS = [8000, 3000]


def run_port_forward(
    *,
    name: Optional[str] = None,
    service: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Forward a local port to a running extension pod."""
    from kamiwaza_sdk import KamiwazaClient
    from kamiwaza_sdk.exceptions import APIError, NotFoundError

    from kamiwaza_extensions.connections import ConnectionManager
    from kamiwaza_extensions.constants import EXTENSIONS_NAMESPACE, extract_user_id

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

    # Fetch extension status and locate a running pod
    from kamiwaza_extensions.constants import ssl_env_override

    pod_name: Optional[str] = None
    svc_name: Optional[str] = None
    resolved_port: Optional[int] = port

    with ssl_env_override(connection):
        client = KamiwazaClient(
            base_url=connection.url, api_key=token.access_token
        )

        try:
            status = client.extensions.get_extension_status(dev_name)
        except NotFoundError as exc:
            console.print(
                f"[red]Error:[/red] Extension '{dev_name}' not found."
            )
            console.print("  Run: [bold]kz-ext dev[/bold] to deploy first.")
            raise typer.Exit(code=1) from exc
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

        # Select target service: match --service flag or pick the first
        # service that has running pods.
        for svc_status in status.services:
            if service and svc_status.name != service:
                continue
            for pod in svc_status.pods:
                if pod.phase == "Running":
                    pod_name = pod.name
                    svc_name = svc_status.name
                    break
            if pod_name:
                break

    if pod_name is None:
        console.print(
            "[red]Error:[/red] No running pod found"
            + (f" for service '{service}'" if service else "")
            + "."
        )
        console.print("  Check status with: [bold]kz-ext status[/bold]")
        raise typer.Exit(code=1)

    # Resolve port: explicit flag > first port from pods info > common ports
    if resolved_port is None:
        # Try to extract port from pod/service extra fields (forward-compat)
        for svc_status in status.services:
            if svc_status.name != svc_name:
                continue
            # pods or service may carry port info as extra fields
            svc_ports = getattr(svc_status, "ports", None)
            if svc_ports and isinstance(svc_ports, list) and len(svc_ports) > 0:
                first = svc_ports[0]
                if isinstance(first, dict):
                    resolved_port = first.get("container_port") or first.get("port")
                elif isinstance(first, int):
                    resolved_port = first
                else:
                    resolved_port = getattr(first, "container_port", None) or getattr(
                        first, "port", None
                    )
            break

    if resolved_port is None:
        resolved_port = _COMMON_PORTS[0]

    # Build kubectl command
    cmd = [
        "kubectl",
        "port-forward",
        "-n",
        EXTENSIONS_NAMESPACE,
        f"pod/{pod_name}",
        f"{resolved_port}:{resolved_port}",
    ]

    console.print(
        f"Forwarding localhost:{resolved_port} → {svc_name}:{resolved_port}"
    )
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")

    # Replace the current process with kubectl port-forward
    try:
        os.execvp("kubectl", cmd)
    except OSError as exc:
        console.print(f"[red]Error:[/red] Failed to run kubectl: {exc}")
        raise typer.Exit(code=1) from exc
