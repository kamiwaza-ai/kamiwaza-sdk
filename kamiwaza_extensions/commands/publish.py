"""Publish command — build, push, and publish extension to catalog."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer
import yaml
from rich.console import Console

from kamiwaza_extensions.catalog_publisher import DEFAULT_CATALOG_SCHEMA
from kamiwaza_extensions.extension_detector import infer_extension_type

console = Console(stderr=True)


APPGARDEN_COMPOSE_FILENAME = "docker-compose.appgarden.yml"


def _load_appgarden_compose(
    ext_dir: Path,
) -> Optional[Tuple[Path, Dict[str, Any]]]:
    """Return ``(path, data)`` for the extension's authored appgarden compose, or None.

    ``docker-compose.appgarden.yml`` is the deployment-ready compose
    produced by an extension's ``sync-compose.py`` (or equivalent). When
    present it is the source of truth for catalog publishing — it
    carries extension-specific transformations (env-shape tweaks,
    hostname rewrites, etc.) that kz-ext's generic ``ComposeTransformer``
    doesn't replicate.

    Falls back to None (caller continues with the generic transform
    against ``docker-compose.yml``) when the file is missing, unparseable,
    or doesn't decode to a mapping.
    """
    candidate = ext_dir / APPGARDEN_COMPOSE_FILENAME
    if not candidate.exists():
        return None
    try:
        data = yaml.safe_load(candidate.read_text())
    except yaml.YAMLError as exc:
        console.print(
            f"[yellow]Warning:[/yellow] failed to parse "
            f"{APPGARDEN_COMPOSE_FILENAME}: {exc} — falling back to "
            "docker-compose.yml + generic transform"
        )
        return None
    if not isinstance(data, dict):
        console.print(
            f"[yellow]Warning:[/yellow] {APPGARDEN_COMPOSE_FILENAME} did not "
            "decode to a mapping — falling back to docker-compose.yml"
        )
        return None
    return candidate, data


def _replace_image_tag(image_ref: str, new_tag: str) -> str:
    """Return *image_ref* with its tag (and any digest) replaced by *new_tag*.

    The namespace (registry + repo path) is preserved verbatim. Handles
    refs that include a registry port (``localhost:5000/foo:tag``) by
    using the position of the last ``/`` to disambiguate the port colon
    from the tag colon, and strips any ``@sha256:...`` suffix before
    re-tagging.
    """
    ref = image_ref.split("@", 1)[0]
    last_slash = ref.rfind("/")
    last_colon = ref.rfind(":")
    if last_colon > last_slash:
        ref = ref[:last_colon]
    return f"{ref}:{new_tag}"


def _retag_appgarden_compose(
    appgarden_data: Dict[str, Any],
    source_compose_data: Optional[Dict[str, Any]],
    *,
    extension_name: str,
    image_tag: str,
    registry: str,
) -> Dict[str, Any]:
    """Rewrite image tags on services that this publish actually owns.

    A service is "owned" by this publish if it has a ``build:`` block in
    the source ``docker-compose.yml`` and is *not* gated by ``profiles:``
    (i.e. ``ImageBuilder`` will build and push an image for it; this
    mirrors the ``buildable_services`` filter ``run_publish`` uses to
    derive ``published_refs``/``digest_map``). For those services we set
    ``image: {registry}/{ext}-{svc}:{image_tag}`` so the catalog points
    at the image we just built with the resolved ``--stage`` / ``--revision``
    tag. External refs (``ghcr.io/.../neo4j``) and any service the
    extension's ``sync-compose.py`` invented are passed through verbatim.

    The appgarden file is otherwise considered deployment-ready by the
    extension's authoring intent and passed through unchanged: host port
    bindings, bind mounts, ``extra_hosts``, ``container_name``,
    top-level ``networks``, env-value placeholders, and the
    ``_ensure_resource_limits`` defaults that ``ComposeTransformer``
    used to backfill are NOT applied here. ``sync-compose.py`` owns that
    shape; if a service is missing ``deploy.resources.limits``,
    ``ComposeValidator`` will surface the warning and the catalog ships
    without the auto-fill.
    """
    out = copy.deepcopy(appgarden_data)
    source_services = (
        source_compose_data.get("services") or {}
        if isinstance(source_compose_data, dict)
        else {}
    )
    # Mirror `buildable_services` (publish.py): exclude `profiles:`-gated
    # services. Without this filter, a `build: + profiles:` service that
    # leaks into the authored appgarden file would be retagged into the
    # catalog while being absent from `published_refs`/`digest_map` — a
    # local-only ref shipping with no corresponding push.
    build_services = {
        name
        for name, svc in source_services.items()
        if isinstance(svc, dict) and "build" in svc and not svc.get("profiles")
    }
    for svc_name, svc in (out.get("services") or {}).items():
        if not isinstance(svc, dict):
            continue
        if svc_name in build_services:
            existing = svc.get("image")
            if isinstance(existing, str) and existing.strip():
                # The appgarden compose's image field is the canonical
                # declaration of where this build's image lives in the
                # registry — set by the extension's sync-compose.py from
                # its docker-compose.yml. We only own the *tag* (stage
                # suffix or --revision SHA); the namespace stays what
                # the extension authored. Without this, extensions whose
                # GHCR path doesn't match the {ext}-{svc} convention
                # silently get the wrong namespace in the catalog.
                svc["image"] = _replace_image_tag(existing, image_tag)
            else:
                # No declared image — fall back to the {ext}-{svc}
                # convention so extensions relying on auto-generated
                # image fields keep working.
                svc["image"] = f"{registry}/{extension_name}-{svc_name}:{image_tag}"
    return out


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


def _verify_supplied_digest(ref: str, supplied: str) -> None:
    """Resolve *ref* in the registry and abort if it disagrees with *supplied*.

    Catches CI typos, stale digests, and the TOCTOU window where a
    parallel run re-pointed the tag between our push and our publish.
    Caller must guarantee the image was just pushed (i.e. ``no_push`` is
    False) — otherwise the registry isn't authoritative for what we
    intended to publish.
    """
    from kamiwaza_extensions.exit_codes import ExitCode
    from kamiwaza_extensions.image_pusher import ImagePushError, ImagePusher

    try:
        actual = ImagePusher.resolve_digest(ref)
    except ImagePushError as exc:
        console.print(f"\n[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if actual != supplied:
        console.print(
            f"\n[red]Error:[/red] supplied --digest does not match "
            f"the registry manifest for {ref}.\n"
            f"  supplied: {supplied}\n"
            f"  registry: {actual}\n"
            "Re-run with the correct digest, or omit --digest to "
            "auto-resolve."
        )
        raise typer.Exit(code=int(ExitCode.VALIDATION))


def _auto_resolve_digests(
    refs: List[str], *, no_build: bool, no_push: bool,
) -> Dict[str, str]:
    """Resolve registry digests for each *ref* and return ``{ref: digest}``.

    Soft-fails for the catalog-only-republish shape (``--no-build
    --no-push``): pre-PR behavior was tag-only with no docker dependency,
    so a resolve failure becomes a warning and the ref is omitted from
    the result rather than aborting the publish.
    """
    from kamiwaza_extensions.image_pusher import ImagePushError, ImagePusher

    digest_map: Dict[str, str] = {}
    for ref in refs:
        try:
            digest_map[ref] = ImagePusher.resolve_digest(ref)
        except ImagePushError as exc:
            if no_build and no_push:
                console.print(
                    f"  [yellow]warn[/yellow] could not resolve digest "
                    f"for {ref}: {exc} — catalog will use tag-only for "
                    "this ref"
                )
                continue
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc
    return digest_map


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
    catalog_schema: int = DEFAULT_CATALOG_SCHEMA,
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
            # The exception text contains the user-supplied digest verbatim;
            # rich console treats `[…]` as markup, so disable interpretation
            # to avoid silent stripping or a markup-injection vector.
            console.print("[red]Error:[/red] ", end="")
            console.print(str(exc), markup=False)
            raise typer.Exit(code=int(ExitCode.VALIDATION)) from exc

    # 1. Detect extension
    detector = ExtensionDetector()
    info = detector.detect()

    if info.compose_data is None:
        console.print("[red]Error:[/red] No docker-compose.yml found.")
        raise typer.Exit(code=1)

    # Prefer the extension's authored appgarden compose when present. It
    # is the deployment-ready output of the extension's `sync-compose.py`
    # and carries extension-specific transformations kz-ext's generic
    # ComposeTransformer doesn't replicate (ENG-4907). Source compose
    # is still used below for ImageBuilder and the buildable-services
    # derivation — those care about `build:` contexts and host shape.
    appgarden_pair = _load_appgarden_compose(info.path)
    if appgarden_pair is not None:
        publish_compose_path, appgarden_data = appgarden_pair
    else:
        publish_compose_path = info.compose_path
        appgarden_data = None

    # Buildable services that will actually be published. Mirrors
    # ComposeTransformer's `profiles:` filter — services with a profiles
    # key are local-only and stripped before catalog construction, so
    # they don't count for buildable-count or hazard checks.
    buildable_services = [
        name
        for name, svc in (info.compose_data.get("services") or {}).items()
        if "build" in svc and not svc.get("profiles")
    ]

    # ENG-4370: --digest pins identity for one image. Multi-image extensions
    # must rely on auto-resolve (the per-service push digest), so reject the
    # ambiguous case before doing any work.
    if digest is not None and len(buildable_services) != 1:
        found = ", ".join(buildable_services) if buildable_services else "none"
        console.print(
            f"[red]Error:[/red] --digest requires exactly one buildable "
            f"service in docker-compose.yml; found {len(buildable_services)} "
            f"({found}). Omit --digest to auto-resolve per-service digests."
        )
        raise typer.Exit(code=int(ExitCode.VALIDATION))

    # Auto-resolve queries the registry post-build. Built-but-not-pushed
    # leaves the registry stale (or empty), so the resolved digest would
    # either error or silently pin the *previous* push.
    # Force the user to either push, skip the build, or supply --digest.
    # Skipped under: dry-run (no build/push happens), and external-only
    # extensions (no buildable services means no hazard).
    if (
        not dry_run
        and not no_build
        and no_push
        and digest is None
        and buildable_services
    ):
        console.print(
            "[red]Error:[/red] --no-push without --no-build cannot pin a "
            "catalog digest — the just-built image is only in your local "
            "daemon. Push first, pass --no-build to publish-only against "
            "the existing registry tag, or supply --digest explicitly."
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
    compose_result = compose_validator.validate(publish_compose_path, info.path)

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

    # 4. Build the catalog-ready compose (uses the stage-aware image tag).
    # When the extension supplied an authored appgarden compose, that file
    # is the source of truth — we only retag the services we actually
    # built. Otherwise run the generic ComposeTransformer against the
    # source docker-compose.yml.
    if appgarden_data is not None:
        transformed = _retag_appgarden_compose(
            appgarden_data,
            info.compose_data,
            extension_name=info.name,
            image_tag=image_tag,
            registry=registry,
        )
    else:
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
        # Mirrors the live path's published_refs derivation: pin against
        # the buildable_services-derived ref so a profiled helper that
        # appears first in dict order can't redirect the user's digest.
        dry_digest_map: Dict[str, str] = {}
        if digest is not None and buildable_services:
            dry_canonical_ref = (
                f"{registry}/{info.name}-{buildable_services[0]}:{image_tag}"
            )
            dry_digest_map[dry_canonical_ref] = digest

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
            publisher = CatalogPublisher(
                profile,
                catalog_schema=catalog_schema,
                extension_dir=info.path,
            )
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

    # 5.5 Preflight: digest resolution post-push needs `docker buildx
    # imagetools`. If buildx is missing on this host, fail before mutating
    # the registry rather than push-then-fail-on-resolve. Only required
    # when push will happen and there's at least one published service to
    # pin (the catalog-only-republish path soft-falls in
    # `_auto_resolve_digests` and doesn't need this guard).
    if not no_push and buildable_services:
        try:
            ImagePusher.check_buildx_available()
        except ImagePushError as exc:
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

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

    # Catalog refs are pinned `image:tag@sha256:...` so they're immutable
    # regardless of tag mutability. published_refs is derived from
    # buildable_services (which excludes profile-only services per
    # ComposeTransformer's filter), so a profiled helper appearing first
    # in image_refs cannot misdirect the digest map. Pass-through external
    # refs keep whatever pinning their source repo applied.
    published_refs: List[str] = [
        f"{registry}/{info.name}-{name}:{image_tag}" for name in buildable_services
    ]
    digest_map: Dict[str, str] = {}
    if published_refs:
        if digest is not None:
            # Single-buildable invariant guaranteed by validation above.
            ref = published_refs[0]
            digest_map[ref] = digest
            if not no_push:
                _verify_supplied_digest(ref, digest)
        else:
            digest_map = _auto_resolve_digests(
                published_refs, no_build=no_build, no_push=no_push,
            )

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
        publisher = CatalogPublisher(
            profile,
            catalog_schema=catalog_schema,
            extension_dir=info.path,
        )
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
    if published_refs:
        # Show what's actually in the catalog (post-profile-filter), pinned
        # where digest_map carries an entry.
        pinned = [
            f"{r}@{digest_map[r]}" if r in digest_map else r
            for r in published_refs
        ]
        console.print(f"  Images:  {', '.join(pinned)}")
    console.print(f"  Catalog: {result.catalog_file}")
