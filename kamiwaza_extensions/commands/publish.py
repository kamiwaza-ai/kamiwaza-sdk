"""Publish command — build, push, and publish extension to catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console

from kamiwaza_extensions.extension_detector import infer_extension_type

console = Console(stderr=True)


def _infer_extension_type(metadata: Dict[str, Any]) -> str:
    """Backwards-compatible alias kept for callers that imported the
    private name. New code should call ``infer_extension_type`` directly
    from ``kamiwaza_extensions.extension_detector`` (single source of
    truth, also used by ``DevLocalRunner`` to gate ``--auth``).
    """
    return infer_extension_type(metadata)


def _resolve_preview_image(
    metadata: Dict[str, Any], extension_dir: Path
) -> Optional[Path]:
    """Return the absolute path to a preview image, or None."""
    preview = metadata.get("preview_image")
    if not preview:
        return None
    resolved = (extension_dir / preview).resolve()
    # Security: reject paths that escape the extension directory
    if not resolved.is_relative_to(extension_dir.resolve()):
        console.print(
            f"[yellow]Warning:[/yellow] preview_image '{preview}' escapes extension directory — ignored"
        )
        return None
    if resolved.exists():
        return resolved
    console.print(
        f"[yellow]Warning:[/yellow] preview_image '{preview}' not found — skipping"
    )
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
    revision: Optional[str] = None,
    digest: Optional[str] = None,
) -> None:
    """Build, push, and publish extension to catalog."""
    from kamiwaza_extensions.catalog_publisher import (
        CatalogDedupError,
        CatalogDedupGuard,
        CatalogPublishError,
        CatalogPublisher,
    )
    from kamiwaza_extensions.compose_transformer import ComposeTransformer
    from kamiwaza_extensions.exit_codes import ExitCode
    from kamiwaza_extensions.extension_detector import ExtensionDetector
    from kamiwaza_extensions.image_builder import ImageBuilder, ImageBuildError
    from kamiwaza_extensions.image_pusher import (
        ImagePushError,
        ImagePusher,
        validate_digest,
    )
    from kamiwaza_extensions.profile_manager import ProfileManager
    from kamiwaza_extensions.registry_builder import RegistryBuilder
    from kamiwaza_extensions.validators.compose import ComposeValidator
    from kamiwaza_extensions.validators.metadata import MetadataValidator

    # 0. Fail fast on bad --revision before any side effects (build, push,
    # registry tag pollution). Previously this validated inside
    # CatalogPublisher.publish, after image push had already happened —
    # invalid revisions like 'foo/bar' or 'BAD CASE' would leak orphan
    # tags into the registry (review re-review PR #84 M2).
    if revision is not None:
        try:
            CatalogDedupGuard.validate_revision(revision)
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(code=int(ExitCode.VALIDATION)) from exc

    # Same fail-fast intent for --digest: reject malformed input before any
    # build/push side effects. Format guard only — buildable-count check
    # happens after compose data is loaded.
    if digest is not None:
        try:
            validate_digest(digest)
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(code=int(ExitCode.VALIDATION)) from exc

    # 1. Detect extension
    detector = ExtensionDetector()
    info = detector.detect()

    if info.compose_data is None:
        console.print("[red]Error:[/red] No docker-compose.yml found.")
        raise typer.Exit(code=1)

    # ENG-4370: --digest pins identity for one image. Multi-image extensions
    # must rely on auto-resolve (the per-service push digest), so reject the
    # ambiguous case before doing any work.
    if digest is not None:
        buildable_services = [
            name for name, svc in (info.compose_data.get("services") or {}).items()
            if "build" in svc
        ]
        if len(buildable_services) != 1:
            found = ", ".join(buildable_services) if buildable_services else "none"
            console.print(
                f"[red]Error:[/red] --digest requires exactly one buildable "
                f"service in docker-compose.yml; found {len(buildable_services)} "
                f"({found}). Omit --digest to auto-resolve per-service digests."
            )
            raise typer.Exit(code=int(ExitCode.VALIDATION))

    version = info.version
    ext_type = _infer_extension_type(info.metadata)

    # Header
    dry_label = " [DRY RUN]" if dry_run else ""
    rev_label = f" rev=[bold]{revision}[/bold]" if revision else ""
    console.print(
        f"Publishing [bold]{info.name}[/bold] v{version}{rev_label} "
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

    # Validate and normalize stage name (used in Docker tags)
    import re as _re
    stage = stage.lower()
    if not _re.match(r"^[a-z0-9][a-z0-9._-]*$", stage):
        console.print(
            f"[red]Error:[/red] Invalid stage name '{stage}'. "
            "Must be lowercase alphanumeric (with hyphens, dots, underscores)."
        )
        raise typer.Exit(code=1)

    # Determine the image tag.
    # --revision (e.g. CI-built SHA-pinned tag) takes precedence over the
    # stage-derived default. Prod publishes use a bare version when no
    # revision is supplied, matching the pre-ENG-3591 convention.
    if revision is not None:
        image_tag = revision
    elif stage == "prod":
        image_tag = version
    else:
        image_tag = f"{version}-{stage}"

    # 4. Transform compose (uses the stage-aware image tag)
    transformer = ComposeTransformer()
    transformed = transformer.transform(
        info.compose_data,
        extension_name=info.name,
        revision_tag=image_tag,
        registry=registry,
    )

    # -- Dry-run path (still runs merge check to detect conflicts) --
    if dry_run:
        short_names = _collect_buildable_image_names(
            info.compose_data, info.name, image_tag, registry
        )
        console.print(
            f"  Would build images:    {', '.join(short_names) if short_names else '(none)'}"
        )
        console.print(f"  Would push to:         {registry}/")

        # Dry-run preview reflects an explicit --digest; auto-resolve
        # is skipped because a dry-run shouldn't talk to the registry.
        dry_digest_map: Dict[str, str] = {}
        if digest is not None:
            dry_refs = _collect_image_refs(
                info.compose_data, info.name, image_tag, registry
            )
            if dry_refs:
                dry_digest_map[dry_refs[0]] = digest

        # Run merge check so dry-run detects version conflicts
        reg_builder = RegistryBuilder()
        entry = reg_builder.build_entry(
            metadata=info.metadata,
            transformed_compose=transformed,
            registry=registry,
            version=version,
            stage=stage,
            revision=revision,
            digest_map=dry_digest_map or None,
        )
        try:
            publisher = CatalogPublisher(profile, extension_dir=info.path)
            result = publisher.publish(
                entry=entry,
                extension_type=ext_type,
                force=force,
                dry_run=True,
                revision=revision,
            )
            console.print(
                f"  Would publish to:      {result.catalog_file} ({result.action})"
            )
        except CatalogDedupError as exc:
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(code=int(ExitCode.VALIDATION)) from exc
        except (CatalogPublishError, ValueError) as exc:
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        console.print()
        console.print("[dim]No changes made (dry-run mode).[/dim]")
        return

    # 5. Build images (using stage-aware tag)
    image_refs: List[str] = []
    if not no_build:
        console.print("  Building images...", end="")
        try:
            builder = ImageBuilder()
            image_refs = builder.build(
                extension_dir=info.path,
                compose_data=info.compose_data,
                extension_name=info.name,
                revision_tag=image_tag,
                registry=registry,
                verbose=verbose,
            )
        except ImageBuildError as exc:
            console.print("    [red]\u2717 build failed[/red]")
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        if image_refs:
            console.print(
                f"    [green]\u2713[/green] {len(image_refs)} image(s) built ({image_tag})"
            )
        else:
            console.print("    [yellow]![/yellow] No images to build")
    else:
        console.print("  [dim]Skipping build (--no-build)[/dim]")
        image_refs = _collect_image_refs(
            info.compose_data, info.name, image_tag, registry
        )

    # 6. Push images (same stage-aware tag)
    if not no_push and image_refs:
        console.print("  Pushing images...", end="")
        # Registry auth: use explicit token from env if provided,
        # otherwise rely on Docker's credential store (docker login).
        import os as _os
        registry_token = _os.environ.get("KZ_PUBLISH_DOCKER_TOKEN") or _os.environ.get("DOCKER_TOKEN")
        try:
            pusher = ImagePusher()
            pusher.push(
                image_refs,
                registry=registry,
                token=registry_token,
                insecure=False,
                verbose=verbose,
            )
        except ImagePushError as exc:
            console.print("    [red]\u2717 push failed[/red]")
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        for ref in image_refs:
            console.print(f"                         [green]\u2713[/green] {ref}")
    elif no_push:
        console.print("  [dim]Skipping push (--no-push)[/dim]")

    # 6.5 Resolve digests for buildable images (ENG-4370). Catalog refs
    # are pinned `image:tag@sha256:...` so they're immutable regardless
    # of tag mutability. Pass-through external/prebuilt-internal refs
    # (postgres, etc.) keep whatever pinning their source repo applied.
    digest_map: Dict[str, str] = {}
    if image_refs:
        if digest is not None:
            # Buildable-count validation above guarantees a single ref.
            digest_map[image_refs[0]] = digest
        else:
            try:
                for ref in image_refs:
                    digest_map[ref] = ImagePusher.resolve_digest(ref)
            except ImagePushError as exc:
                console.print(f"\n[red]Error:[/red] {exc}")
                raise typer.Exit(code=1) from exc

    # 7. Build catalog entry
    reg_builder = RegistryBuilder()
    entry = reg_builder.build_entry(
        metadata=info.metadata,
        transformed_compose=transformed,
        registry=registry,
        version=version,
        stage=stage,
        revision=revision,
        digest_map=digest_map or None,
    )

    # 8. Publish to catalog
    console.print("  Publishing catalog...", end="")
    preview_image_path = _resolve_preview_image(info.metadata, info.path)

    try:
        publisher = CatalogPublisher(profile, extension_dir=info.path)
        result = publisher.publish(
            entry=entry,
            extension_type=ext_type,
            force=force,
            dry_run=False,
            preview_image_path=preview_image_path,
            revision=revision,
        )
    except CatalogDedupError as exc:
        console.print("  [red]\u2717 publish rejected[/red]")
        console.print(f"\n[red]Error:[/red] {exc}")
        raise typer.Exit(code=int(ExitCode.VALIDATION)) from exc
    except CatalogPublishError as exc:
        console.print("  [red]\u2717 publish failed[/red]")
        console.print(f"\n[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print("  [red]\u2717 publish failed[/red]")
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
