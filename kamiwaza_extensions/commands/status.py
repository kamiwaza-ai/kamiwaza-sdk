"""Status command — show extension deployment status."""

from __future__ import annotations

from typing import Any, Optional

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
    from kamiwaza_extensions.dev_state import read_state

    extension_name: Optional[str] = None

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
        extension_name = info.name

        # Prefer the dev-state file's `last_dev_name` (written by `kz-ext dev`
        # — see §4.2.9 / ENG-3887). Falls back to deriving the name from the
        # current user's JWT, which only matches when the same user deployed.
        #
        # Cluster-change guard (review re-review PR #84 H2): the dev-state
        # captures the cluster the prior deploy hit. After
        # `kz-ext login <other-cluster>`, the saved name belongs to the OLD
        # cluster — querying the new cluster for it would return a
        # misleading 404. Fall back to the JWT-derived name when the saved
        # cluster doesn't match the active connection so the new cluster
        # is queried for the correct (deterministic) dev name.
        #
        # Deployer guard (review re-re-review PR #84 H2): dev names are
        # per-user (the JWT subject seeds the hash). If user A deployed
        # from this checkout and user B then runs `kz-ext status` after
        # logging in as themselves, querying for A's saved name would
        # return A's deployment to B — confusing in the best case, a
        # misleading 404 in the worst. Require the saved deployer to
        # match the current JWT email before reusing the name.
        #
        # decode_email lives in dev_state (review re-re-re-review PR #84
        # M2) — `commands.status` doesn't reach into `commands.dev`.
        from kamiwaza_extensions.dev_state import decode_email

        state = read_state(info.path)
        current_email = decode_email(token.access_token)
        same_cluster = bool(state and state.cluster == connection.url)
        # Email match is case-insensitive (review re-re-re-review PR #84
        # M4) — some IdPs vary casing across token refreshes.
        same_deployer = bool(
            state and current_email and state.deployer.lower() == current_email.lower()
        )
        if state and state.last_dev_name and same_cluster and same_deployer:
            dev_name = state.last_dev_name
        else:
            dev_name = PayloadBuilder.make_dev_name(
                info.name, user_id=extract_user_id(token.access_token)
            )
    else:
        dev_name = name

    from kamiwaza_extensions.constants import ssl_env_override

    with ssl_env_override(connection):
        client = KamiwazaClient(base_url=connection.url, api_key=token.access_token)

        # P10 fix: hit /api/extensions/{name} (always present) instead of
        # /api/extensions/{name}/status (404 on every cluster currently
        # deployed). The Extension response covers everything kz-ext status
        # needs to surface; status-detail tables fall back to the rich
        # endpoint only when it's available.
        try:
            ext = client.extensions.get_extension(dev_name)
        except APIError as exc:
            if exc.status_code == 404:
                console.print(f"[red]Error:[/red] Extension '{dev_name}' not found.")
                console.print("  Run: [bold]kz-ext dev[/bold] to deploy first.")
                raise typer.Exit(code=1) from exc
            raise

        # Display header
        console.print(f"Extension:  [bold]{ext.name}[/bold]")
        console.print(f"Phase:      {ext.phase or 'Unknown'}")
        url = ext.endpoints.external if ext.endpoints else None
        if url:
            console.print(f"URL:        [blue]{url}[/blue]")

        # Surface deployer annotation (§4.2.9 DeployedImageAnnotation).
        # Namespace is ``kamiwaza.io/*`` per ENG-3901 / F-010 — the platform's
        # annotation filter only persists ``kamiwaza.io/*`` keys.
        deployer = _read_annotation(ext, "kamiwaza.io/deployer")
        if deployer:
            console.print(f"Last deployed by: [bold]{deployer}[/bold]")
        deployed_at = _read_annotation(ext, "kamiwaza.io/deployed-at")
        if deployed_at:
            console.print(f"Deployed at:      {deployed_at}")
        revision = _read_annotation(ext, "kamiwaza.io/revision")
        if revision:
            console.print(f"Revision:         {revision}")
        console.print()

        # Services table
        svc_table = Table(title="Services")
        svc_table.add_column("NAME", style="bold")
        svc_table.add_column("READY")
        svc_table.add_column("REPLICAS")
        svc_table.add_column("STATE")

        for svc in ext.services:
            svc_table.add_row(
                svc.name,
                "yes" if svc.ready else "no",
                f"{svc.available_replicas}/{svc.replicas}",
                svc.message or "",
            )

        console.print(svc_table)

        # Catalog overlay shadow (ENG-6802) — only in auto-detect mode,
        # where the extension's catalog name is known.
        if extension_name:
            _print_overlay_status(client, extension_name)


# Nudge once a shadow is old enough that "my env silently runs a stale
# dev build" becomes the likely failure mode.
SHADOW_STALENESS_DAYS = 7


def _print_overlay_status(client: Any, extension_name: str) -> None:
    """Show this extension's local catalog overlay shadow, if any.

    Tolerates platforms without overlay support (pre-ENG-6802): any API
    failure renders nothing — the overlay line is informational.
    """
    from kamiwaza_extensions.catalog_overlay import list_overlays

    try:
        overlays = list_overlays(client)
    except Exception:
        return

    shadow = next(
        (o for o in overlays if o.get("template_name") == extension_name), None
    )
    if shadow is None:
        return

    build = shadow.get("git_sha") or shadow.get("shadow_version") or "unknown"
    branch = shadow.get("git_branch")
    branch_label = f" (branch {branch})" if branch else ""
    if shadow.get("dirty"):
        build = f"{build}, dirty tree"

    shadows_version = shadow.get("shadows_version")
    shadowing = (
        f"shadowing upstream {shadows_version}"
        if shadows_version
        else "no upstream catalog entry"
    )

    age_days = _age_days(shadow.get("updated_at"))
    age_label = f", published {_format_age(age_days)}" if age_days is not None else ""

    console.print()
    console.print(
        f"Catalog overlay: local dev build [bold]{build}[/bold]{branch_label}, "
        f"{shadowing}{age_label}"
    )
    console.print("  [dim]New workrooms launch this build. Undo: kz-ext dev --unload[/dim]")
    if age_days is not None and age_days >= SHADOW_STALENESS_DAYS:
        console.print(
            f"  [yellow]This shadow is {age_days} days old — re-run "
            "kz-ext dev for a fresh build, or kz-ext dev --unload to "
            "fall back to the catalog.[/yellow]"
        )


def _age_days(timestamp: Optional[str]) -> Optional[int]:
    """Days elapsed since an ISO timestamp, or None when unparseable."""
    if not timestamp or not isinstance(timestamp, str):
        return None
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - parsed
    return max(delta.days, 0)


def _format_age(days: int) -> str:
    if days == 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


def _read_annotation(ext: Any, key: str) -> Optional[str]:
    """Read an annotation off an Extension response.

    Annotations may live under ``ext.annotations`` (Pydantic-modelled) or
    on the raw dict surfaced by ``ext.model_extra`` (forward-compat). Try
    both shapes so this works regardless of where the platform places them.
    """
    annotations = getattr(ext, "annotations", None)
    if isinstance(annotations, dict):
        val = annotations.get(key)
        if isinstance(val, str) and val:
            return val
    extra = getattr(ext, "model_extra", None) or {}
    nested = extra.get("annotations") if isinstance(extra, dict) else None
    if isinstance(nested, dict):
        val = nested.get(key)
        if isinstance(val, str) and val:
            return val
    return None
