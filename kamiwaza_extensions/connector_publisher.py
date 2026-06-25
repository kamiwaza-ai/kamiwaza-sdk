"""Publish a connector extension to the connectors catalog (``connectors.json``).

Connectors differ from apps/tools in two ways, so they get their own publish
path rather than the compose pipeline in ``commands/publish.py``:

- **No docker-compose.** A connector's catalog entry IS its self-describing
  manifest (the ``ConnectorSpec.to_manifest()`` shape: ``connector_type``,
  ``oauth``, ``egress_allowlist``, ``deployment``, ``config_schema``, ``icon``),
  authored in ``kamiwaza.json`` under a ``manifest`` object.
- **kz-ext does not build the image.** The connector's own Dockerfile/CI builds
  and pushes it; kz-ext resolves that pre-pushed image's digest, pins it, and
  merges the manifest entry into ``connectors.json`` via the shared,
  type-agnostic :class:`CatalogPublisher` (same lock/backup/dedup/verify path
  apps and tools use).

So a connector repo "mimics" an app/tool by shipping a ``kamiwaza.json`` with
``type: "connector"`` + the manifest; ``ExtensionDetector`` discovers it like
any other extension and ``kz-ext publish`` routes it here.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console

from kamiwaza_extensions.catalog_publisher import DEFAULT_CATALOG_SCHEMA
from kamiwaza_extensions.extension_detector import ExtensionInfo

console = Console(stderr=True)

# Deployment-descriptor keys the operator needs to schedule the connector pod.
_REQUIRED_DEPLOYMENT_KEYS = ("image_repository", "image_registry", "image_tag")


def _fail(message: str) -> None:
    console.print(f"\n[red]Error:[/red] {message}")
    raise typer.Exit(code=1)


def _validate_manifest(info: ExtensionInfo) -> dict[str, Any]:
    """Return the connector manifest from ``kamiwaza.json``, or exit on error."""
    manifest = info.metadata.get("manifest")
    if not isinstance(manifest, dict):
        _fail(
            f"{info.name}: a connector's kamiwaza.json must carry a 'manifest' "
            "object (the ConnectorSpec.to_manifest() shape)."
        )
    if not manifest.get("connector_type"):
        _fail(f"{info.name}: manifest.connector_type is required.")
    deployment = manifest.get("deployment")
    missing = (
        [k for k in _REQUIRED_DEPLOYMENT_KEYS if not (isinstance(deployment, dict) and deployment.get(k))]
    )
    if missing:
        _fail(
            f"{info.name}: manifest.deployment must include "
            f"{', '.join(_REQUIRED_DEPLOYMENT_KEYS)} (missing: {', '.join(missing)})."
        )
    return manifest


def _image_ref(deployment: dict[str, Any]) -> str:
    return (
        f"{deployment['image_registry']}/"
        f"{deployment['image_repository']}:{deployment['image_tag']}"
    )


def build_connector_entry(
    info: ExtensionInfo,
    manifest: dict[str, Any],
    *,
    pinned_digest: str | None = None,
) -> dict[str, Any]:
    """Build the connectors.json entry: the manifest + name/version (+ pinned digest).

    ``name``/``version`` are what :class:`CatalogPublisher` keys dedup/merge on
    (shared with apps/tools); ``ConnectorSpec.from_manifest`` ignores them
    (``extra="allow"``). When *pinned_digest* is set it is recorded on the
    deployment descriptor so the catalog entry is immutable.
    """
    deployment = dict(manifest["deployment"])
    if pinned_digest:
        deployment["image_digest"] = pinned_digest
    return {
        **manifest,
        "deployment": deployment,
        "name": info.name,
        "version": info.version,
    }


def publish_connector(
    info: ExtensionInfo,
    *,
    stage: str,
    dry_run: bool = False,
    force: bool = False,
    no_push: bool = False,
    revision: str | None = None,
    digest: str | None = None,
    catalog_schema: int = DEFAULT_CATALOG_SCHEMA,
) -> None:
    """Publish one detected connector extension to ``connectors.json``."""
    from kamiwaza_extensions.catalog_publisher import (
        CatalogDedupError,
        CatalogPublisher,
        CatalogPublishError,
    )
    from kamiwaza_extensions.exit_codes import ExitCode
    from kamiwaza_extensions.profile_manager import ProfileManager

    manifest = _validate_manifest(info)
    image_ref = _image_ref(manifest["deployment"])

    dry_label = " [DRY RUN]" if dry_run else ""
    console.print(
        f"Publishing connector [bold]{info.name}[/bold] v{info.version} "
        f"to profile [bold]'{stage}'[/bold]...{dry_label}"
    )

    try:
        profile = ProfileManager().resolve_profile(stage, extension_dir=info.path)
    except ValueError as exc:
        console.print(f"\n[red]Error:[/red] {exc}")
        console.print(
            "  Run: [bold]kz-ext config publish-profile <name> "
            "--registry ... --catalog-endpoint ...[/bold]"
        )
        raise typer.Exit(code=1) from exc

    # Pin the pre-pushed image. kz-ext doesn't build connector images, so the
    # image must already be in the registry (pushed by the connector's CI).
    # Resolution doubles as an existence gate. Skipped on dry-run / --no-push
    # (no registry round-trip) and when --digest is supplied explicitly.
    pinned = digest
    if pinned is None and not dry_run and not no_push:
        from kamiwaza_extensions.image_pusher import ImagePusher, ImagePushError

        try:
            pinned = ImagePusher.resolve_digest(image_ref)
        except ImagePushError as exc:
            console.print(f"\n[red]Error:[/red] {exc}")
            _fail(
                f"could not resolve a digest for {image_ref}. Build + push the "
                "connector image first, pass --digest, or use --no-push to "
                "publish the manifest against the existing tag."
            )

    entry = build_connector_entry(info, manifest, pinned_digest=pinned)

    console.print("  Publishing catalog...", end="")
    try:
        publisher = CatalogPublisher(
            profile, catalog_schema=catalog_schema, extension_dir=info.path
        )
        result = publisher.publish(
            entry=entry,
            extension_type="connector",
            force=force,
            dry_run=dry_run,
            revision=revision,
        )
    except CatalogDedupError as exc:
        console.print("  [red]✗ publish rejected[/red]")
        console.print(f"\n[red]Error:[/red] {exc}")
        raise typer.Exit(code=int(ExitCode.VALIDATION)) from exc
    except (CatalogPublishError, ValueError) as exc:
        console.print("  [red]✗ publish failed[/red]")
        console.print(f"\n[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if dry_run:
        console.print(
            f"  Would publish to:      {result.catalog_file} ({result.action})"
        )
        console.print()
        console.print("[dim]No changes made (dry-run mode).[/dim]")
        return

    console.print(
        f"  [green]✓[/green] connector {info.name} v{info.version} "
        f"published ({result.action})"
    )
    console.print()
    console.print(
        f"Published [bold]{info.name}[/bold] v{info.version} to "
        f"{profile.catalog_bucket}"
    )
    console.print(f"  Image:   {image_ref}{('@' + pinned) if pinned else ''}")
    console.print(f"  Catalog: {result.catalog_file}")
