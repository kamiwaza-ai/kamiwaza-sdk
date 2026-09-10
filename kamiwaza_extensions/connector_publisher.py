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
from rich.markup import escape as escape_markup

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
    missing = [
        k
        for k in _REQUIRED_DEPLOYMENT_KEYS
        if not (isinstance(deployment, dict) and deployment.get(k))
    ]
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


def _fail_image_resolution(image_ref: str, exc: Exception) -> None:
    """Abort with an actionable, markup-escaped image-resolution error.

    Registry errors and digests contain ``[a-f0-9]`` runs that rich would parse as
    markup tags and silently garble, so the dynamic parts are escaped (the app
    publish path does the same).
    """
    console.print(
        f"\n[red]Error:[/red] could not resolve a digest for "
        f"[bold]{escape_markup(image_ref)}[/bold]: {escape_markup(str(exc))}"
    )
    console.print(
        "  The image must exist in the registry before catalog publish. Common causes:\n"
        "    • the connector image hasn't been built + pushed yet (push it first)\n"
        "    • not logged in to the registry for a private image (log in first)\n"
        "    • a transient registry outage (retry)\n"
        "  Or pass --digest sha256:... to pin a known digest, or --no-push to "
        "publish the manifest against the existing tag."
    )
    raise typer.Exit(code=1)


def _verify_supplied_digest(image_ref: str, supplied: str) -> None:
    """Resolve *image_ref* and abort if it disagrees with the supplied ``--digest``.

    Mirrors the app publish path: a supplied digest is verified against the
    registry, not trusted blind, so a CI typo / stale digest / TOCTOU re-point
    cannot pin an unpullable, immutable catalog entry. ``ImagePushError`` from the
    resolve propagates to the caller's :func:`_fail_image_resolution`.
    """
    from kamiwaza_extensions.exit_codes import ExitCode
    from kamiwaza_extensions.image_pusher import ImagePusher

    actual = ImagePusher.resolve_digest(image_ref)
    if actual != supplied:
        console.print(
            "\n[red]Error:[/red] supplied --digest does not match the registry "
            f"manifest for [bold]{escape_markup(image_ref)}[/bold].\n"
            f"  supplied: {escape_markup(supplied)}\n"
            f"  registry: {escape_markup(actual)}\n"
            "  Re-run with the correct digest, or omit --digest to auto-resolve."
        )
        raise typer.Exit(code=int(ExitCode.VALIDATION))


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
    # A supplied revision is the build's canonical image tag and overrides the
    # manifest's deployment.image_tag, which is only the static authoring default
    # (e.g. "develop"). Without this, a publish for any stage would resolve and
    # pin that authoring-default image rather than the one the build produced.
    # Applied before digest resolution so both the pinned digest and the
    # published catalog entry reflect the revision tag (matching how the app
    # publish path rewrites its compose image refs).
    if revision and manifest["deployment"].get("image_tag") != revision:
        # The revision changes the tag, so any digest the manifest authored
        # against the old tag no longer applies; drop it (a fresh digest is
        # resolved below). A manifest already rendered with this exact tag needs
        # no change — it keeps its tag and its matching digest.
        deployment = {**manifest["deployment"], "image_tag": revision}
        deployment.pop("image_digest", None)
        manifest = {**manifest, "deployment": deployment}
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

    # Pin the pre-pushed image so the catalog entry is immutable. kz-ext doesn't
    # build connector images, so the image must already be in the registry (pushed
    # by the connector's CI); resolution doubles as an existence gate. Resolution
    # is a read-only registry lookup, so --no-push (don't re-push an
    # already-pushed image) must NOT skip it on its own — otherwise the entry is
    # written tag-only and mutable. The four cases mirror the app publish path:
    #   - dry run: skip (no registry round-trip).
    #   - --no-push WITH an explicit --digest: publish-only escape hatch — trust
    #     the precomputed digest, no buildx/registry needed.
    #   - --digest without --no-push: verify the supplied digest against the registry.
    #   - otherwise (incl. --no-push without --digest): resolve from the registry.
    pinned = digest
    if not dry_run and not (no_push and digest is not None):
        from kamiwaza_extensions.image_pusher import ImagePusher, ImagePushError

        try:
            # Fail fast before any registry round-trip if buildx (which
            # resolve_digest shells out to) is missing — matches the app path.
            ImagePusher.check_buildx_available()
            if digest is None:
                pinned = ImagePusher.resolve_digest(image_ref)
            else:
                _verify_supplied_digest(image_ref, digest)
        except ImagePushError as exc:
            _fail_image_resolution(image_ref, exc)

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
