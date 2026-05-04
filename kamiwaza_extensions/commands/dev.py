"""Dev remote command — build, push, and deploy to Kamiwaza cluster."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import typer
from rich.console import Console

console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Helpers extracted for unit testing — review re-review PR #84 H1 + H4
# ---------------------------------------------------------------------------


def _build_patch_kwargs(
    patch_services: List[Any],
    payload: Any,
) -> Dict[str, Any]:
    """Build the kwargs dict for ``PatchExtension(**kwargs)`` from a
    ``CreateExtension`` payload.

    Carries the ``deployer``/``revision``/``deployed-at`` annotations
    from the payload's ``model_extra`` so PATCH redeploys refresh CRD
    metadata — ``kz-ext status`` would otherwise show stale ``Last
    deployed by`` after the first redeploy (review re-review PR #84 H1).
    """
    kwargs: Dict[str, Any] = {"services": patch_services}
    annotations = (payload.model_extra or {}).get("annotations")
    if annotations:
        kwargs["annotations"] = annotations
    return kwargs


# Match the default ``RevisionTagger.generate_tag`` format:
# ``{version}-dev-{sha7+}.{epoch}`` where the sha portion is the git short
# SHA (typically 7-12 hex chars). The epoch suffix is the only thing that
# changes between same-code invocations; stripping it gives us a stable
# identity we can use as the resume key.
#
# Note: the leading ``.+`` is greedy — for pathological revisions like
# ``feature-dev-foo-dev-abc1234.5`` the regex anchors on the *rightmost*
# ``-dev-`` occurrence. Determinism preserved (review re-re-re-review M5)
# so if the user supplied a custom tag containing ``-dev-`` they still
# get a stable identity, just one anchored at the last segment.
_CLEAN_REV_RE = re.compile(r"^(.+-dev-[0-9a-f]{4,40})\.\d+$")


def _stable_revision_id(rev_tag: str) -> Optional[str]:
    """Return the stable portion of a revision tag, or ``None`` when
    resume would be unsafe.

    Three cases (review re-review PR #84 H1 / re-re-review):

      * ``{version}-dev-{sha7+}.{epoch}`` — clean tree at a specific
        commit. Strip the ``.{epoch}`` suffix; the SHA fully identifies
        the source code, so two invocations of the same commit produce
        identical stable ids and can resume each other safely.
      * ``{version}-dev-dirty.{epoch}`` or ``-dev-nogit.{epoch}`` —
        the SHA is unknown or the tree is dirty, so two invocations
        could carry different content under the same ``dirty`` /
        ``nogit`` slug. Return ``None`` to refuse resume rather than
        silently redeploy stale code.
      * Anything else — assume it's a custom ``--revision`` value the
        user passed explicitly; use it verbatim. The user opted into
        the identity by pinning, so ``rev-1 == rev-1`` is intentional.
    """
    if "-dev-dirty." in rev_tag or "-dev-nogit." in rev_tag:
        return None
    m = _CLEAN_REV_RE.match(rev_tag)
    if m:
        return m.group(1)
    return rev_tag


def _is_resumable(
    prior_state: Any,
    rev_tag: str,
    connection_url: str,
    sdk_repo: Optional[str] = None,
    service: Optional[str] = None,
    registry: str = "",
) -> bool:
    """Return True when the prior dev-state can resume the current run.

    Resume requires every input that selects what gets built/pushed/
    deployed to match the prior run. If any differs, the prior build
    artifacts are not the right answer for this invocation:

      * **Source identity** (``rev_tag``) — see :func:`_stable_revision_id`
        for the clean-sha vs dirty/nogit handling. Different code = full
        pipeline.
      * **Cluster** — the prior push points at a specific registry/CR;
        a new cluster has its own registry, so the cached image isn't
        there.
      * **Service filter** (``--service``) — a partial-service first run
        only built that one service. A later full run would happily
        deploy un-built services with tags that were never pushed
        (review re-re-re-review PR #84 H1).
      * **SDK override** (``--sdk-repo``) — the SDK code is mutable
        between runs even when the extension's git SHA is unchanged.
        Skipping build would silently redeploy stale SDK content.
        Conservatively: any non-equal sdk_repo (including None vs set)
        invalidates resume.
      * **Registry** (``KAMIWAZA_REGISTRY`` / derived) — the prior push
        targeted a specific registry; a different registry means the
        image isn't there to skip-push to.
    """
    if prior_state is None:
        return False
    current_id = _stable_revision_id(rev_tag)
    prior_id = _stable_revision_id(prior_state.last_revision or "")
    if current_id is None or prior_id is None:
        return False
    if current_id != prior_id:
        return False
    if prior_state.cluster != connection_url:
        return False
    # Service filter, sdk_repo, and registry must all match. None vs ""
    # are treated as equivalent for service/sdk_repo (older state files
    # didn't record them — refuse resume on those by mismatching against
    # current values when current is set).
    if (prior_state.last_service or None) != (service or None):
        return False
    if (prior_state.last_sdk_repo or None) != (sdk_repo or None):
        return False
    if prior_state.last_registry != registry:
        return False
    return True


def _decode_email(access_token: str) -> Optional[str]:
    """Compatibility shim — the real implementation lives in
    :mod:`kamiwaza_extensions.dev_state` so ``commands.status`` doesn't
    reach into a sibling command (review re-re-re-review PR #84 M2).
    Existing internal callers and tests can still reference this name.
    """
    from kamiwaza_extensions.dev_state import decode_email

    return decode_email(access_token)


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




def _delete_and_recreate(client, dev_name, payload, console):
    """Legacy fallback: delete the old extension and re-create it.

    Used when the platform does not support PATCH.
    """
    import time

    from kamiwaza_sdk.exceptions import APIError, NotFoundError
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
        except NotFoundError:
            pass  # Deleted — proceed to create
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
    sdk_repo: Optional[str] = None,
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
    from kamiwaza_extensions.dev_state import (
        DevState,
        mark_step,
        read_state,
        resume_message,
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

    # 4. Derive registry — must happen before the resume check so we can
    # compare the active registry against the one persisted in dev-state.
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

    # Read prior dev-state for resume hints (§4.2.9 DevStateFile, ENG-3887).
    # If the prior run wrote matching inputs (revision, cluster, service,
    # sdk-repo, registry) and got past a step, skip that step on this
    # invocation. Different revision/cluster/service/sdk-repo/registry =
    # different image content or destination = full pipeline (review
    # re-re-re-review PR #84 H1).
    prior_state = read_state(info.path)
    notice = resume_message(prior_state)
    if notice:
        console.print(f"[dim]{notice}[/dim]")
    resumable = _is_resumable(
        prior_state,
        rev_tag,
        connection.url,
        sdk_repo=sdk_repo,
        service=service,
        registry=registry,
    )
    if resumable and not no_build and prior_state.is_step_complete("build"):
        console.print(
            f"[dim]Skipping build — revision {rev_tag} already built in prior run.[/dim]"
        )
        no_build = True
    if resumable and not no_push and prior_state.is_step_complete("push"):
        console.print(
            f"[dim]Skipping push — revision {rev_tag} already pushed in prior run.[/dim]"
        )
        no_push = True

    # Print header
    console.print(f"  Extension:  [bold]{info.name}[/bold] ({info.version})")
    console.print(f"  Connection: {connection.name} ({connection.url})")
    console.print(f"  Revision:   {rev_tag}")
    # Surface auto-disabled TLS verify when the URL is a dev TLD so the
    # user knows why their KAMIWAZA_TLS_REJECT_UNAUTHORIZED ends up "0".
    # Skip the notice when the persisted setting already matched (no
    # effective change) or when the user set the env var explicitly.
    if (
        connection.verify_ssl
        and not connection.effective_verify_ssl()
        and os.environ.get("KAMIWAZA_VERIFY_SSL", "").strip().lower() != "false"
    ):
        console.print(
            f"  [dim]TLS verify auto-disabled for dev hostname "
            f"({urlparse(connection.url).hostname or connection.url}); set "
            f"KAMIWAZA_VERIFY_SSL=true to enforce.[/dim]"
        )
    console.print()

    # 5. Transform compose
    transformer = ComposeTransformer()
    transformed = transformer.transform(
        info.compose_data,
        extension_name=info.name,
        revision_tag=rev_tag,
        registry=registry,
    )

    # 5b. Resolve SDK override for build
    build_overrides = None
    if sdk_repo and not no_build:
        from kamiwaza_extensions.sdk_override import (
            SdkOverrideSpec,
            build_typescript_lib,
            check_buildkit_available,
            generate_build_overrides,
            print_override_diagnostics,
            resolve_sdk_override,
            validate_sdk_override,
        )

        override_spec = resolve_sdk_override(sdk_repo, info.path)
        if override_spec:
            validation = validate_sdk_override(override_spec)
            for err in validation.errors:
                console.print(f"[red]SDK override error: {err}[/red]")
            for warn in validation.warnings:
                console.print(f"[yellow]SDK override: {warn}[/yellow]")

            if not validation.ok:
                console.print("[red]SDK override disabled due to errors above[/red]")
            elif not check_buildkit_available():
                console.print(
                    "[red]Error:[/red] SDK override for remote deploy requires Docker BuildKit.\n"
                    "  Fix: Upgrade Docker to 20.10+ or set DOCKER_BUILDKIT=1\n"
                    "  Alternatively, use [bold]kz-ext dev local --sdk-repo[/bold] for local dev."
                )
                raise typer.Exit(code=1)
            else:
                # Build TS if needed
                if override_spec.typescript and (
                    override_spec.build_typescript
                    or not override_spec.typescript_dist_path.is_dir()
                ):
                    if not build_typescript_lib(override_spec):
                        console.print("[yellow]Continuing without TypeScript override[/yellow]")
                        override_spec = SdkOverrideSpec(
                            sdk_repo=override_spec.sdk_repo,
                            python=override_spec.python,
                            typescript=False,
                            build_typescript=False,
                        )

                print_override_diagnostics(override_spec)
                build_overrides = generate_build_overrides(
                    override_spec, info.compose_data, extension_dir=info.path,
                )
        console.print()

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
                build_overrides=build_overrides,
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
    deployer_email = _decode_email(token.access_token)
    payload = payload_builder.build(
        metadata=info.metadata,
        transformed_compose=transformed,
        connection=connection,
        dev_name=dev_name,
        deployer=deployer_email,
        revision=rev_tag,
    )

    def _record(step: str) -> None:
        try:
            mark_step(
                info.path,
                step,
                revision=rev_tag,
                dev_name=dev_name,
                cluster=connection.url,
                extension_name=info.name,
                deployer=deployer_email or "",
                # Persist the resume-key inputs so the next invocation can
                # detect when service-filter / sdk-repo / registry differ
                # (review re-re-re-review PR #84 H1).
                service=service,
                sdk_repo=sdk_repo,
                registry=registry,
            )
        except OSError as state_exc:
            console.print(
                f"[dim]Warning: could not write dev-state.json: {state_exc}[/dim]"
            )

    # Mark prior steps complete based on what's already done by this point
    # in the function. (Build + push happen above; record them now so a
    # crash during apply leaves a usable resume hint.)
    if not no_build:
        _record("build")
    if not no_push and image_refs:
        _record("push")

    # 9. Deploy, poll, and print URL
    from kamiwaza_extensions.constants import ssl_env_override
    console.print(f"Deploying to {connection.url}...")
    with ssl_env_override(connection):
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
            # Carries the deployer/revision/deployed-at annotations on
            # every PATCH so `kz-ext status` reflects the current
            # redeploy (review re-review PR #84 H1).
            patch = PatchExtension(**_build_patch_kwargs(patch_services, payload))

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

        _record("apply")

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
            # P9 (ENG-3887): print the dev-suffixed name even on timeout so
            # the user can locate the partial deployment via kz-ext status.
            console.print(f"\n[bold]Deployment name:[/bold] {dev_name}")
            from kamiwaza_extensions.dev_diagnostics import diagnose_dev_timeout
            from kamiwaza_extensions.constants import EXTENSIONS_NAMESPACE
            from kamiwaza_extensions.exit_codes import ExitCode

            diagnosis = diagnose_dev_timeout(
                dev_name, EXTENSIONS_NAMESPACE, connection_url=connection.url,
            )
            console.print(f"[red]Error:[/red] {exc}")
            console.print(f"  [dim]{diagnosis.message}[/dim]")
            if diagnosis.fix:
                console.print(f"  [dim]Fix: {diagnosis.fix}[/dim]")
            exit_code = (
                int(ExitCode.CLUSTER_NOT_READY)
                if diagnosis.category == "operator-not-ready"
                else 1
            )
            raise typer.Exit(code=exit_code) from exc
        except DeploymentFailedError as exc:
            # P9: print the dev-suffixed name on failure too.
            console.print(f"\n[bold]Deployment name:[/bold] {dev_name}")
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        _record("poll")

        # 11. Print URL + dev-suffixed name (P9: always show the name on
        # any terminal state so `kz-ext status <name>` is one copy-paste away).
        url = ext.endpoints.external if ext.endpoints else None
        console.print("\n  [green]\u2713[/green] Rollout complete")
        console.print()
        console.print(f"[bold]{info.name}[/bold] is running as [bold]{dev_name}[/bold] at:")
        if url:
            console.print(f"  [blue]{url}[/blue]")
        else:
            console.print("  [dim](no external URL reported — check kz-ext status)[/dim]")
