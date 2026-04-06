"""Publish command — build, push, and publish extension to catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console

console = Console(stderr=True)


def _infer_extension_type(metadata: Dict[str, Any]) -> str:
    """Determine extension type from metadata or naming convention.

    Checks ``type``, ``template_type``, then falls back to name-prefix
    heuristics.  Returns ``"app"`` if nothing matches.
    """
    explicit = metadata.get("type") or metadata.get("template_type")
    if explicit in ("app", "tool", "service"):
        return explicit

    name = metadata.get("name", "")
    if name.startswith("tool-") or name.startswith("mcp-"):
        return "tool"
    if name.startswith("service-"):
        return "service"

    return "app"


def _resolve_preview_image(
    metadata: Dict[str, Any], extension_dir: Path
) -> Optional[Path]:
    """Return the absolute path to a preview image, or None."""
    preview = metadata.get("preview_image")
    if not preview:
        return None
    resolved = extension_dir / preview
    if resolved.exists():
        return resolved
    return None


def _collect_buildable_image_names(
    compose_data: Dict[str, Any],
    extension_name: str,
    version: str,
    registry: str,
) -> List[str]:
    """Return short image names (``name:tag``) for services with build contexts."""
    names: List[str] = []
    for svc_name, svc in (compose_data.get("services") or {}).items():
        if "build" in svc:
            names.append(f"{extension_name}-{svc_name}:{version}")
    return names


def _collect_image_refs(
    compose_data: Dict[str, Any],
    extension_name: str,
    version: str,
    registry: str,
) -> List[str]:
    """Return full image refs for services with build contexts."""
    refs: List[str] = []
    for svc_name, svc in (compose_data.get("services") or {}).items():
        if "build" in svc:
            refs.append(f"{registry}/{extension_name}-{svc_name}:{version}")
    return refs


def run_publish(
    *,
    stage: str,
    dry_run: bool = False,
    force: bool = False,
    no_build: bool = False,
    no_push: bool = False,
    verbose: bool = False,
) -> None:
    """Build, push, and publish extension to catalog."""
    from kamiwaza_extensions.catalog_publisher import CatalogPublisher, CatalogPublishError
    from kamiwaza_extensions.compose_transformer import ComposeTransformer
    from kamiwaza_extensions.extension_detector import ExtensionDetector
    from kamiwaza_extensions.image_builder import ImageBuilder, ImageBuildError
    from kamiwaza_extensions.image_pusher import ImagePusher, ImagePushError
    from kamiwaza_extensions.profile_manager import ProfileManager
    from kamiwaza_extensions.registry_builder import RegistryBuilder
    from kamiwaza_extensions.validators.compose import ComposeValidator
    from kamiwaza_extensions.validators.metadata import MetadataValidator

    # 1. Detect extension
    detector = ExtensionDetector()
    info = detector.detect()

    if info.compose_data is None:
        console.print("[red]Error:[/red] No docker-compose.yml found.")
        raise typer.Exit(code=1)

    version = info.version
    ext_type = _infer_extension_type(info.metadata)

    # Header
    dry_label = " [DRY RUN]" if dry_run else ""
    console.print(
        f"Publishing [bold]{info.name}[/bold] v{version} "
        f"to profile [bold]'{stage}'[/bold]...{dry_label}"
    )
    console.print()

    # 2. Validate
    console.print("  Validating...", end="")
    meta_validator = MetadataValidator()
    meta_result = meta_validator.validate(info.path / "kamiwaza.json")

    compose_validator = ComposeValidator()
    compose_result = compose_validator.validate(info.compose_path, info.path)

    all_errors = meta_result.errors[:]
    all_warnings = meta_result.warnings[:]
    if compose_result:
        all_errors.extend(compose_result.errors)
        all_warnings.extend(compose_result.warnings)

    if all_errors:
        console.print("          [red]\u2717 failed[/red]")
        for err in all_errors:
            console.print(f"    [red]\u2717[/red] {err}")
        raise typer.Exit(code=1)

    if all_warnings:
        console.print("          [green]\u2713[/green] passed")
        for warn in all_warnings:
            console.print(f"    [yellow]![/yellow] {warn}")
    else:
        console.print("          [green]\u2713[/green] passed")

    # 3. Resolve publish profile
    profile_mgr = ProfileManager()
    try:
        profile = profile_mgr.resolve_profile(stage, extension_dir=info.path)
    except ValueError as exc:
        console.print(f"\n[red]Error:[/red] {exc}")
        console.print(
            "  Run: [bold]kz-ext config publish-profile <name> --registry ... --catalog-endpoint ...[/bold]"
        )
        raise typer.Exit(code=1) from exc

    registry = profile.registry

    # 4. Transform compose
    transformer = ComposeTransformer()
    transformed = transformer.transform(
        info.compose_data,
        extension_name=info.name,
        revision_tag=version,
        registry=registry,
    )

    # -- Dry-run path --
    if dry_run:
        short_names = _collect_buildable_image_names(
            info.compose_data, info.name, version, registry
        )
        console.print(
            f"  Would build images:    {', '.join(short_names) if short_names else '(none)'}"
        )
        console.print(f"  Would push to:         {registry}/")
        catalog_file = f"{profile.catalog_bucket}/{_catalog_s3_key(ext_type)}"
        console.print(f"  Would publish to:      {catalog_file}")
        console.print()
        console.print("[dim]No changes made (dry-run mode).[/dim]")
        return

    # 5. Build images
    image_refs: List[str] = []
    if not no_build:
        console.print("  Building images...", end="")
        try:
            builder = ImageBuilder()
            image_refs = builder.build(
                extension_dir=info.path,
                compose_data=info.compose_data,
                extension_name=info.name,
                revision_tag=version,
                registry=registry,
                verbose=verbose,
            )
        except ImageBuildError as exc:
            console.print(f"    [red]\u2717 build failed[/red]")
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        if image_refs:
            console.print(
                f"    [green]\u2713[/green] {len(image_refs)} image(s) built ({version})"
            )
        else:
            console.print("    [yellow]![/yellow] No images to build")
    else:
        console.print("  [dim]Skipping build (--no-build)[/dim]")
        image_refs = _collect_image_refs(
            info.compose_data, info.name, version, registry
        )

    # 6. Push images
    if not no_push and image_refs:
        console.print("  Pushing images...", end="")
        try:
            pusher = ImagePusher()
            pusher.push(
                image_refs,
                registry=registry,
                token=None,
                insecure=False,
                verbose=verbose,
            )
        except ImagePushError as exc:
            console.print(f"    [red]\u2717 push failed[/red]")
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        for ref in image_refs:
            console.print(f"                         [green]\u2713[/green] {ref}")
    elif no_push:
        console.print("  [dim]Skipping push (--no-push)[/dim]")

    # 7. Build catalog entry
    reg_builder = RegistryBuilder()
    entry = reg_builder.build_entry(
        metadata=info.metadata,
        transformed_compose=transformed,
        registry=registry,
        version=version,
        stage=stage if stage in ("prod", "stage", "dev") else "prod",
    )

    # 8. Publish to catalog
    console.print("  Publishing catalog...", end="")
    preview_image_path = _resolve_preview_image(info.metadata, info.path)

    try:
        publisher = CatalogPublisher(profile)
        result = publisher.publish(
            entry=entry,
            extension_type=ext_type,
            force=force,
            dry_run=False,
            preview_image_path=preview_image_path,
        )
    except CatalogPublishError as exc:
        console.print(f"  [red]\u2717 publish failed[/red]")
        console.print(f"\n[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(f"  [red]\u2717 publish failed[/red]")
        console.print(f"\n[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"  [green]\u2713[/green] version {result.version} published ({result.action})"
    )

    # 9. Summary
    console.print()
    console.print(
        f"Published [bold]{info.name}[/bold] v{version} to {profile.catalog_bucket}"
    )
    if image_refs:
        console.print(f"  Images:  {', '.join(image_refs)}")
    console.print(f"  Catalog: {result.catalog_file}")


def _catalog_s3_key(ext_type: str) -> str:
    """Return the S3 key path portion for the catalog file."""
    type_file_map = {
        "app": "apps.json",
        "tool": "tools.json",
        "service": "apps.json",
    }
    type_file = type_file_map.get(ext_type, "apps.json")
    return f"garden/v2/{type_file}"
