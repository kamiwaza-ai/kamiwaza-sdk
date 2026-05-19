"""Shell command — open an interactive shell in an extension pod."""

from __future__ import annotations

import os
import subprocess
from typing import Optional

import typer
from rich.console import Console

console = Console(stderr=True)


def _find_running_pod_via_kubectl(
    dev_name: str,
    service: Optional[str],
) -> Optional[str]:
    """Use kubectl label selection as a fallback pod lookup."""
    from kamiwaza_extensions.constants import EXTENSIONS_NAMESPACE

    label = f"extensions.kamiwaza.io/deployment-id={dev_name}"
    if service:
        label += f",extensions.kamiwaza.io/service={service}"

    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                EXTENSIONS_NAMESPACE,
                "-l",
                label,
                "-o",
                "jsonpath={range .items[*]}{.metadata.name}{'\\t'}{.status.phase}{'\\n'}{end}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        console.print(
            "[red]Error:[/red] kubectl not found. Install it to open a shell."
        )
        raise typer.Exit(code=1)
    except subprocess.TimeoutExpired:
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        pod_name, _, phase = line.partition("\t")
        if pod_name and phase == "Running":
            return pod_name
    return None


def run_shell(
    *,
    name: Optional[str] = None,
    service: Optional[str] = None,
) -> None:
    """Open an interactive shell in an extension pod."""
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
        console.print("[red]Error:[/red] Token expired. Run: [bold]kz-ext login[/bold]")
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

    # Get pod name via status endpoint
    from kamiwaza_extensions.constants import ssl_env_override

    pod_name = None
    with ssl_env_override(connection):
        client = KamiwazaClient(base_url=connection.url, api_key=token.access_token)
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
        except NotFoundError as exc:
            console.print(f"[red]Error:[/red] Extension '{dev_name}' not found.")
            console.print("  Run: [bold]kz-ext dev[/bold] to deploy first.")
            raise typer.Exit(code=1) from exc
        except APIError as exc:
            if exc.status_code == 404:
                console.print(f"[red]Error:[/red] Extension '{dev_name}' not found.")
                console.print("  Run: [bold]kz-ext dev[/bold] to deploy first.")
                raise typer.Exit(code=1) from exc
            # Other errors: try to proceed without pod name
            console.print(
                f"[yellow]Warning:[/yellow] Could not fetch extension status: {exc}"
            )
        except Exception as exc:
            console.print(
                f"[yellow]Warning:[/yellow] Could not determine target pod: {exc}"
            )
    if pod_name is None:
        pod_name = _find_running_pod_via_kubectl(dev_name, service)
    if pod_name is None:
        console.print(
            "[red]Error:[/red] No running pod found"
            + (f" for service '{service}'" if service else "")
            + "."
        )
        console.print("  Check status with: [bold]kz-ext status[/bold]")
        raise typer.Exit(code=1)

    cmd = [
        "kubectl",
        "exec",
        "-it",
        "-n",
        EXTENSIONS_NAMESPACE,
        pod_name,
        "--",
        "/bin/sh",
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
