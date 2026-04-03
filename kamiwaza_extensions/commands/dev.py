"""Dev remote command — build, push, and deploy to Kamiwaza cluster."""

from __future__ import annotations

import os
import subprocess
from typing import Optional
from urllib.parse import urlparse

import typer
from rich.console import Console

console = Console(stderr=True)


def _detect_kind_registry() -> Optional[str]:
    """Auto-detect a Kind local registry via the ``local-registry-hosting`` configmap.

    Returns ``localhost:<port>`` if found, else ``None``.
    """
    try:
        result = subprocess.run(
            [
                "kubectl", "get", "configmap", "local-registry-hosting",
                "-n", "kube-public",
                "-o", "jsonpath={.data.localRegistryHosting\\.v1}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        # Parse the YAML-ish output: host: "host.docker.internal:5001"
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("host:"):
                host_val = line.split(":", 1)[1].strip().strip('"').strip("'")
                # Map host.docker.internal to localhost (it may not resolve on the host)
                parsed = urlparse(f"//{host_val}")
                port = parsed.port or 5001
                console.print(f"[dim]Auto-detected Kind local registry: localhost:{port}[/dim]")
                return f"localhost:{port}"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None




def _delete_and_recreate(client, dev_name, payload, console) -> "Extension":
    """Legacy fallback: delete the old extension and re-create it.

    Used when the platform does not support PATCH.
    """
    import time

    from kamiwaza_sdk.exceptions import APIError
    from kamiwaza_sdk.schemas.extensions import Extension

    console.print("  [dim]Replacing existing deployment...[/dim]")
    try:
        client.extensions.delete_extension(dev_name)
    except Exception as del_exc:
        console.print(f"  [dim]Delete failed: {del_exc}[/dim]")

    ext: Extension | None = None
    for attempt in range(15):
        time.sleep(2)
        try:
            client.extensions.get_extension(dev_name)
            continue  # Still exists — keep waiting
        except Exception:
            pass  # Deleted
        try:
            ext = client.extensions.create_extension(payload)
            console.print("  [green]\u2713[/green] Extension replaced")
            break
        except APIError as retry_exc:
            if retry_exc.status_code == 409 and attempt < 14:
                continue  # Finalizer still running, retry
            console.print(f"[red]Error:[/red] Deploy failed: {retry_exc}")
            raise typer.Exit(code=1) from retry_exc

    if ext is None:
        console.print("[red]Error:[/red] Timed out waiting for old deployment to be removed")
        raise typer.Exit(code=1)
    return ext


def run_dev_remote(
    *,
    no_build: bool = False,
    no_push: bool = False,
    service: Optional[str] = None,
    revision: Optional[str] = None,
    verbose: bool = False,
) -> None:
    """Build, push, and deploy extension to a Kamiwaza cluster."""
    from kamiwaza_sdk import KamiwazaClient
    from kamiwaza_sdk.exceptions import APIError

    from kamiwaza_extensions.compose_transformer import ComposeTransformer
    from kamiwaza_extensions.connections import ConnectionManager
    from kamiwaza_extensions.deployment_poller import (
        DeploymentFailedError,
        DeploymentPoller,
        DeploymentTimeoutError,
    )
    from kamiwaza_extensions.extension_detector import ExtensionDetector
    from kamiwaza_extensions.image_builder import ImageBuilder, ImageBuildError
    from kamiwaza_extensions.image_pusher import ImagePusher, ImagePushError
    from kamiwaza_extensions.payload_builder import PayloadBuilder
    from kamiwaza_extensions.revision_tagger import RevisionTagger

    # 1. Detect extension
    detector = ExtensionDetector()
    info = detector.detect()

    if info.compose_data is None:
        console.print("[red]Error:[/red] No docker-compose.yml found.")
        raise typer.Exit(code=1)

    # 2. Resolve connection + auth
    conn_mgr = ConnectionManager()
    connection = conn_mgr.get_active_connection()
    if connection is None:
        console.print("[red]Error:[/red] No Kamiwaza connection configured.")
        console.print("  Run: [bold]kz-ext login <url>[/bold]")
        raise typer.Exit(code=1)

    token = conn_mgr.get_token()
    if token is None:
        console.print("[red]Error:[/red] Connection token expired or missing.")
        console.print("  Run: [bold]kz-ext login[/bold] to re-authenticate.")
        raise typer.Exit(code=1)

    # 3. Generate revision tag
    tagger = RevisionTagger()
    rev_tag = tagger.generate_tag(info.version, custom=revision)

    # Warn about dirty tree
    if revision is None:
        sha, dirty = tagger.get_git_info()
        if dirty:
            console.print(
                "[yellow]Warning:[/yellow] Working tree has uncommitted changes "
                "-- image tagged with 'dirty'."
            )

    # 4. Derive registry
    registry = os.environ.get("KAMIWAZA_REGISTRY")
    if not registry:
        registry = _detect_kind_registry()
    if not registry:
        # Fallback: convention registry.{cluster-domain}
        cluster_url = connection.url.removesuffix("/api")
        parsed = urlparse(cluster_url)
        if parsed.hostname:
            registry = f"registry.{parsed.hostname}"
        else:
            console.print("[red]Error:[/red] Could not derive registry from connection URL.")
            raise typer.Exit(code=1)

    # Print header
    console.print(f"  Extension:  [bold]{info.name}[/bold] ({info.version})")
    console.print(f"  Connection: {connection.name} ({connection.url})")
    console.print(f"  Revision:   {rev_tag}")
    console.print()

    # 5. Transform compose
    transformer = ComposeTransformer()
    transformed = transformer.transform(
        info.compose_data,
        extension_name=info.name,
        revision_tag=rev_tag,
        registry=registry,
    )

    # 6. Build images
    if not no_build:
        console.print("Building images...")
        try:
            builder = ImageBuilder()
            image_refs = builder.build(
                extension_dir=info.path,
                compose_data=info.compose_data,  # Original compose (has build contexts)
                extension_name=info.name,
                revision_tag=rev_tag,
                registry=registry,
                service_filter=service,
                verbose=verbose,
            )
        except ImageBuildError as exc:
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        if not image_refs:
            console.print("[yellow]Warning:[/yellow] No images to build (no services with build contexts).")
        console.print()
    else:
        console.print("[dim]Skipping build (--no-build)[/dim]")
        # Collect expected image refs for push
        image_refs = []
        for svc_name, svc in (info.compose_data.get("services") or {}).items():
            if service and svc_name != service:
                continue
            if "build" in svc:
                image_refs.append(f"{registry}/{info.name}-{svc_name}:{rev_tag}")
        console.print()

    # 7. Push images
    if not no_push and image_refs:
        console.print(f"Pushing to {registry}...")
        try:
            pusher = ImagePusher()
            pusher.push(
                image_refs,
                registry=registry,
                token=token.access_token,
                insecure=not connection.verify_ssl,
                verbose=verbose,
            )
        except ImagePushError as exc:
            console.print(f"\n[red]Error:[/red] {exc}")
            console.print("  Run: [bold]kz-ext doctor[/bold] to check connection and registry access.")
            raise typer.Exit(code=1) from exc
        console.print()
    elif no_push:
        console.print("[dim]Skipping push (--no-push)[/dim]\n")

    # 8. Build API payload
    payload_builder = PayloadBuilder()
    from kamiwaza_extensions.constants import extract_user_id
    dev_name = PayloadBuilder.make_dev_name(info.name, user_id=extract_user_id(token.access_token))
    payload = payload_builder.build(
        metadata=info.metadata,
        transformed_compose=transformed,
        connection=connection,
        dev_name=dev_name,
    )

    # 9. Deploy, poll, and print URL — wrapped in try/finally for SSL env restore
    console.print(f"Deploying to {connection.url}...")
    old_verify_ssl = os.environ.get("KAMIWAZA_VERIFY_SSL")
    if not connection.verify_ssl:
        os.environ["KAMIWAZA_VERIFY_SSL"] = "false"
    try:
        client = KamiwazaClient(
            base_url=connection.url,
            api_key=token.access_token,
        )

        # Deploy — PATCH if exists, POST if new
        from kamiwaza_sdk.exceptions import NotFoundError
        from kamiwaza_sdk.schemas.extensions import (
            ImagePatch,
            PatchExtension,
            PatchServiceSpec,
        )

        try:
            # Check if extension already exists
            client.extensions.get_extension(dev_name)

            # Build patch from payload — extract image, env, replicas per service
            patch_services = []
            for svc in payload.services:
                # Split tag after last '/' to avoid confusing registry port with tag
                image = svc.image
                slash_pos = image.rfind("/")
                after_slash = image[slash_pos + 1:] if slash_pos >= 0 else image
                if ":" in after_slash:
                    tag = after_slash.rsplit(":", 1)[1]
                else:
                    tag = "latest"
                spec = PatchServiceSpec(
                    name=svc.name,
                    image=ImagePatch(tag=tag),
                )
                if svc.env:
                    spec.env = svc.env
                if svc.replicas is not None:
                    spec.replicas = svc.replicas
                patch_services.append(spec)
            patch = PatchExtension(services=patch_services)

            try:
                ext = client.extensions.patch_extension(dev_name, patch)
                console.print("  [green]\u2713[/green] Extension updated (zero-downtime)")
            except APIError as patch_exc:
                if patch_exc.status_code == 405:
                    # Platform doesn't support PATCH yet — fall back
                    console.print(
                        "  [yellow]Warning:[/yellow] Platform does not support PATCH. "
                        "Falling back to delete+create."
                    )
                    ext = _delete_and_recreate(client, dev_name, payload, console)
                else:
                    console.print(f"[red]Error:[/red] Deploy failed: {patch_exc}")
                    raise typer.Exit(code=1) from patch_exc

        except NotFoundError:
            # Extension doesn't exist — create
            ext = client.extensions.create_extension(payload)
            console.print("  [green]\u2713[/green] Extension created")

        # 10. Poll for readiness
        try:
            timeout = int(os.environ.get("KAMIWAZA_DEV_TIMEOUT", "300"))
        except ValueError:
            console.print("[yellow]Warning:[/yellow] Invalid KAMIWAZA_DEV_TIMEOUT, using 300s")
            timeout = 300
        poller = DeploymentPoller()
        try:
            ext = poller.wait_for_ready(client, dev_name, timeout=timeout)
        except DeploymentTimeoutError as exc:
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc
        except DeploymentFailedError as exc:
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        # 11. Print URL
        url = ext.endpoints.external if ext.endpoints else None
        console.print(f"\n  [green]\u2713[/green] Rollout complete")
        console.print()
        console.print(f"[bold]{info.name}[/bold] is running at:")
        if url:
            console.print(f"  [blue]{url}[/blue]")
        else:
            console.print(f"  [dim](no external URL reported — check kz-ext status)[/dim]")
    finally:
        if old_verify_ssl is None:
            os.environ.pop("KAMIWAZA_VERIFY_SSL", None)
        else:
            os.environ["KAMIWAZA_VERIFY_SSL"] = old_verify_ssl
