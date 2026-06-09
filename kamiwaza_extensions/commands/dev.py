"""Dev remote command — build, push, and deploy to Kamiwaza cluster."""

from __future__ import annotations

import os
import re
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

    Carries forward fields whose values may have changed between
    deploys but whose CRs persist beyond the first CREATE:

    1. ``annotations`` — the ``deployer``/``revision``/``deployed-at``
       trio plus ``service-ref-rewrites`` (cross-service URL map). PR
       #84 H1 originally added the trio so ``kz-ext status`` doesn't
       show stale ``Last deployed by`` after redeploys; the rewrites
       map joined later (PR #92 iter-6) for the same reason — without
       it, an extension first CREATE'd by the old SDK keeps its empty
       annotations forever even after the user upgrades.
    2. ``kamiwaza`` integration spec — ``tlsRejectUnauthorized``,
       ``apiUrl``, ``origin``, ``useAuth``. Same problem class:
       changing TLS verify on the host (or upgrading SDK so dev-TLD
       auto-disable kicks in) doesn't take effect until the user
       deletes the existing extension. CRs are long-lived; PATCH must
       carry these or iterative dev silently runs against stale config.
    """
    kwargs: Dict[str, Any] = {"services": patch_services}
    extra = payload.model_extra or {}
    annotations = extra.get("annotations")
    if annotations:
        kwargs["annotations"] = annotations
    # ``kamiwaza`` is a top-level field on CreateExtension; mirror it onto
    # the patch via ``extra="allow"`` so PATCH refreshes the persisted CR.
    if getattr(payload, "kamiwaza", None) is not None:
        kwargs["kamiwaza"] = payload.kamiwaza
    # ``sandbox`` rides ``CreateExtension`` as an extra field
    # (``extra="allow"``). Forward it on PATCH too: ``kz-ext dev`` uses
    # PATCH after the first CREATE, and the operator needs the sandbox
    # contract refreshed for sandbox RBAC reconciliation when a user
    # toggles ``SANDBOX_BACKEND=kubernetes`` or changes
    # namespace/whitelist/resources on a redeploy.
    sandbox = extra.get("sandbox")
    if sandbox:
        kwargs["sandbox"] = sandbox
    # Always forward ``volumes`` (even when empty) so removing a named
    # volume from compose actually clears the stale top-level volume on
    # the persisted CR. Same iterative-dev contract that drives the
    # ``kamiwaza``/annotations forwarding above.
    #
    # Safe against operator-managed mounts: the kamiwaza-extension-
    # operator rebuilds each Deployment's volume list every reconcile as
    # ``[tmp emptyDir] + (data PVC if persistence) + svc.Volumes``. The
    # ``tmp``/``data`` volumes are injected at reconcile time and are
    # never stored in ``svc.Volumes``, so PATCHing ``volumes: []`` clears
    # only user-declared volumes and cannot wipe operator-managed ones.
    kwargs["volumes"] = extra.get("volumes") or []
    return kwargs


def _build_patch_service_specs(payload: Any) -> List[Any]:
    """Build the per-service ``PatchServiceSpec`` list from a
    ``CreateExtension`` payload, forwarding the new ``x-kamiwaza``
    per-service overrides via ``extra="allow"``.

    The PATCH carries the full ``(registry, repository, tag)`` triple
    from the canonical image ref. The operator reconstructs the CR's
    image field from those three, so a repository change between
    deploys — common when an extension's declared image namespace
    differs from the legacy ``{ext}-{svc}`` form that pre-fix kz-ext
    would have written — flows through. Sending only ``tag`` would
    leave the CR's image field at the original repository and produce
    ``ImagePullBackOff`` on the next pull.
    """
    from kamiwaza_extensions.compose_transformer import _split_image_ref
    from kamiwaza_sdk.schemas.extensions import ImagePatch, PatchServiceSpec

    patch_services: List[Any] = []
    for svc in payload.services:
        registry, repository, tag = _split_image_ref(svc.image)
        image_patch = ImagePatch(
            tag=tag,
            registry=registry,
            repository=repository,
        )
        spec = PatchServiceSpec(
            name=svc.name,
            image=image_patch,
        )
        if svc.env:
            spec.env = svc.env
        if svc.replicas is not None:
            spec.replicas = svc.replicas
        svc_extra = svc.model_extra or {}
        for field in (
            "healthCheck",
            "automountServiceAccountToken",
            "containerSecurityContext",
        ):
            if field in svc_extra and svc_extra[field] is not None:
                setattr(spec, field, svc_extra[field])
        # Always forward ``volumeMounts`` (even when empty) so removing
        # a volume from compose clears the stale per-service mount on
        # the persisted CR; consistent with the top-level ``volumes``
        # forwarding in ``_build_patch_kwargs``. The operator appends
        # ``svc.VolumeMounts`` after its own ``tmp``/``data`` mounts, so
        # an empty list clears only user-declared mounts.
        spec.volumeMounts = svc_extra.get("volumeMounts") or []
        patch_services.append(spec)
    return patch_services


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
    push_registry: str = "",
    image_basename: Optional[str] = None,
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
      * **Registry / push registry** (``KAMIWAZA_REGISTRY`` /
        ``KAMIWAZA_PUSH_REGISTRY`` / derived) — the prior push targeted
        specific image and push registry addresses; a different address
        means the image isn't there to skip-push to.
      * **image_basename** — kamiwaza.json override that controls the
        ``{registry}/{basename}-{svc}:{tag}`` legacy-fallback synthesis.
        Build/push and deploy refs depend on it, so a flipped override
        under the same ``--revision`` must invalidate resume.
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
    # Service filter, sdk_repo, registry, push_registry, and image_basename must all
    # match. None vs "" are treated as equivalent for the Optional
    # fields (older state files didn't record them — refuse resume on
    # those by mismatching against current values when current is set).
    if (prior_state.last_service or None) != (service or None):
        return False
    if (prior_state.last_sdk_repo or None) != (sdk_repo or None):
        return False
    if prior_state.last_registry != registry:
        return False
    current_push_registry = push_registry or registry
    prior_push_registry = prior_state.last_push_registry or prior_state.last_registry
    if prior_push_registry != current_push_registry:
        return False
    if (prior_state.last_image_basename or None) != (image_basename or None):
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
    """Compatibility wrapper for tests and callers that patch this helper."""

    from kamiwaza_extensions.registry_resolution import detect_kind_registry

    return detect_kind_registry()


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
        console.print(
            "[red]Error:[/red] Timed out waiting for old deployment to be removed"
        )
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
    from kamiwaza_extensions.compose_transformer import (
        ComposeTransformer,
        compute_canonical_refs,
    )
    from kamiwaza_extensions.connections import ConnectionManager
    from kamiwaza_extensions.deployment_poller import (
        DeploymentFailedError,
        DeploymentPoller,
        DeploymentTimeoutError,
    )
    from kamiwaza_extensions.dev_state import (
        mark_step,
        read_state,
        resume_message,
    )
    from kamiwaza_extensions.extension_detector import ExtensionDetector
    from kamiwaza_extensions.image_builder import ImageBuilder, ImageBuildError
    from kamiwaza_extensions.image_pusher import ImagePusher, ImagePushError
    from kamiwaza_extensions.payload_builder import PayloadBuilder
    from kamiwaza_extensions.revision_tagger import RevisionTagger
    from kamiwaza_sdk import KamiwazaClient
    from kamiwaza_sdk.exceptions import APIError

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

    # 4. Derive image and push registries — must happen before the resume
    # check so we can compare the active destinations against dev-state.
    # Engine selection comes first so the resolver can choose a VM alias
    # the active engine can actually resolve (R6 — `host.docker.internal`
    # only resolves inside the Docker daemon's VM; podman from host CLI
    # cannot resolve it at all).
    from kamiwaza_extensions.registry_resolution import (
        BUILD_VM_LOOPBACK_ALIAS_SOURCE,
        build_push_ref_map,
        docker_accepts_insecure_push_to,
        insecure_registry_daemon_json_fix,
        is_loopback_registry,
        resolve_dev_registries,
        select_push_engine,
    )

    # Derive `insecure` from the *effective* verify-SSL setting (env override /
    # dev-hostname auto-disable / persisted flag), not the persisted flag alone.
    # When TLS is auto-disabled for a dev URL but `verify_ssl` is still True,
    # the persisted flag would select the secure Docker push path and skip the
    # insecure-registry pre-flight -- then Docker attempts HTTPS against the
    # plain-HTTP loopback registry and the push fails (ENG-5719 follow-up).
    insecure = not connection.effective_verify_ssl()
    # ImageBuilder is Docker-only today. On a normal/fresh run, the push must
    # use Docker too; otherwise Docker builds the image into Docker's store and
    # a Podman push cannot see it. Explicit --no-build pushes still use the
    # auto-selected engine because the user is asserting the image already
    # exists in the active engine's store.
    build_engine = "docker"
    push_engine = (
        build_engine if not no_build else select_push_engine(insecure=insecure)
    )

    try:
        registry_resolution = resolve_dev_registries(
            connection,
            kind_registry_detector=_detect_kind_registry,
            push_engine=push_engine,
        )
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    registry = registry_resolution.image_registry
    push_registry = registry_resolution.push_registry

    # Read prior dev-state for resume hints (§4.2.9 DevStateFile, ENG-3887).
    # If the prior run wrote matching inputs (revision, cluster, service,
    # sdk-repo, registry, push registry) and got past a step, skip that
    # step on this invocation. Different inputs = different image content
    # or destination = full pipeline (review re-re-re-review PR #84 H1).
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
        push_registry=push_registry,
        image_basename=info.image_basename,
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

    # Engine consistency: Docker and Podman keep separate image stores, so
    # a ``--no-build`` resume that would push with a different engine than
    # the build used would call ``podman tag <docker-image>`` (or the
    # inverse) on an image the new engine can't see (jxstanford iter-4
    # High #1). Refuse with an actionable error rather than letting
    # ImagePusher fail downstream with a confusing tag-not-found message.
    if (
        no_build
        and not no_push
        and resumable
        and prior_state is not None
        and prior_state.last_build_engine
        and prior_state.last_build_engine != push_engine
    ):
        console.print(
            f"[red]Error:[/red] Previous build used '{prior_state.last_build_engine}' "
            f"but this push will use '{push_engine}'.\n"
            "  Their image stores are separate, so the prior image isn't visible to "
            f"'{push_engine}'.\n"
            "  Rerun [bold]kz-ext dev[/bold] without --no-build to rebuild with the "
            "active engine, or restore the previous engine "
            f"(e.g., start Docker Desktop if it was '{prior_state.last_build_engine}')."
        )
        raise typer.Exit(code=1)

    # Print header
    console.print(f"  Extension:  [bold]{info.name}[/bold] ({info.version})")
    console.print(f"  Connection: {connection.name} ({connection.url})")
    console.print(f"  Revision:   {rev_tag}")
    console.print(
        f"  Registry:   {registry} ({registry_resolution.image_registry_source})"
    )
    if push_registry != registry:
        console.print(
            f"  Push via:   {push_registry} "
            f"({registry_resolution.push_registry_source})"
        )
    # Surface auto-disabled TLS verify when the URL is a dev TLD so the
    # user knows why their KAMIWAZA_TLS_REJECT_UNAUTHORIZED ends up "0".
    # Skip the notice when the persisted setting already matched (no
    # effective change) or when the user set the env var explicitly.
    from kamiwaza_extensions.connections import _VERIFY_SSL_FALSE_VALUES

    if (
        connection.verify_ssl
        and not connection.effective_verify_ssl()
        and os.environ.get("KAMIWAZA_VERIFY_SSL", "").strip().lower()
        not in _VERIFY_SSL_FALSE_VALUES
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
        image_basename=info.image_basename,
    )
    transformed = transformer.resolve_env_placeholders(transformed)

    # Canonical image refs for every build-context service. Single
    # source of truth shared with the transformed compose so the image
    # we build and push matches the ref the K8s payload will pull. The
    # transformer honors a service's declared image namespace; without
    # this map ImageBuilder would synthesize the legacy {ext}-{svc}
    # form and ship a pod referencing an image that was never pushed.
    canonical_refs: Dict[str, str] = compute_canonical_refs(
        info.compose_data.get("services") or {},
        registry=registry,
        extension_name=info.name,
        revision_tag=rev_tag,
        image_basename=info.image_basename,
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
                        console.print(
                            "[yellow]Continuing without TypeScript override[/yellow]"
                        )
                        override_spec = SdkOverrideSpec(
                            sdk_repo=override_spec.sdk_repo,
                            python=override_spec.python,
                            typescript=False,
                            build_typescript=False,
                        )

                print_override_diagnostics(override_spec)
                build_overrides = generate_build_overrides(
                    override_spec,
                    info.compose_data,
                    extension_dir=info.path,
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
                image_refs=canonical_refs,
                image_basename=info.image_basename,
            )
        except ImageBuildError as exc:
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        if not image_refs:
            console.print(
                "[yellow]Warning:[/yellow] No images to build (no services with build contexts)."
            )
        console.print()
    else:
        console.print("[dim]Skipping build (--no-build)[/dim]")
        # Collect expected image refs for push from the canonical map so
        # --no-build pushes hit the same registry path the deployment
        # payload will reference.
        if service:
            image_refs = [canonical_refs[service]] if service in canonical_refs else []
        else:
            image_refs = list(canonical_refs.values())
        console.print()

    # 7. Push images
    if not no_push and image_refs:
        push_ref_map = build_push_ref_map(
            image_refs,
            image_registry=registry,
            push_registry=push_registry,
        )
        push_uses_auto_loopback_alias = (
            registry_resolution.push_registry_source == BUILD_VM_LOOPBACK_ALIAS_SOURCE
        )
        # Pre-flight: when docker is the active engine pushing insecurely to
        # the rewritten alias, the daemon must treat that alias as insecure
        # (default ``insecure-registries`` only covers 127.0.0.0/8). Fail fast
        # with a one-line daemon.json fix (jxstanford iter-4 Critical #1)
        # rather than letting the push timeout against HTTPS. Gated inside the
        # push branch -- after resume may have set ``no_push`` and once
        # ``image_refs`` is known -- so a resume-skip or a build-context-less
        # extension can't trip it when no push will happen (ENG-5719
        # follow-up). Gate on the auto loopback-alias rewrite so a legitimate
        # user-supplied secure-HTTPS push override isn't refused just because
        # the active Kamiwaza connection itself is insecure. Also require at
        # least one actual retag to that alias; declared external image refs may
        # leave the push-ref map empty even when registry resolution found an
        # alias for fallback refs.
        if (
            insecure
            and push_engine == "docker"
            and push_registry != registry
            and push_uses_auto_loopback_alias
            and push_ref_map
            and not docker_accepts_insecure_push_to(push_registry)
        ):
            console.print(
                f"[red]Error:[/red] {insecure_registry_daemon_json_fix(push_registry)}"
            )
            raise typer.Exit(code=1)

        console.print(f"Pushing to {push_registry}...")
        try:
            # The local Kamiwaza dev registry is a stock anonymous
            # ``registry:2`` — it requires no auth, and the connection token
            # is a Kamiwaza *API* credential, not a registry credential. Skip
            # the registry login for it. Beyond being unnecessary, the login
            # is actively broken on the macOS podman-machine topology
            # (ENG-5719): ``podman login <vm-alias>`` resolves the registry
            # host client-side and fails ("no such host"), while
            # ``podman push <vm-alias>`` resolves it inside the VM and
            # succeeds. Skip login for a loopback push target or for the
            # auto-generated loopback VM alias. Authenticated user overrides
            # (e.g. ``KAMIWAZA_PUSH_REGISTRY=registry.example.com``) are
            # non-loopback and still log in even when the image registry is
            # loopback.
            registry_login_token = (
                None
                if (
                    is_loopback_registry(push_registry)
                    or (
                        push_uses_auto_loopback_alias and is_loopback_registry(registry)
                    )
                )
                else token.access_token
            )
            pusher = ImagePusher()
            pusher.push(
                image_refs,
                registry=push_registry,
                token=registry_login_token,
                # Use the *effective* verify-SSL -- the same value engine
                # selection and the insecure-registry pre-flight derived above
                # (env override / dev-hostname auto-disable / persisted flag),
                # not the persisted flag alone. Otherwise the resolver
                # validates one engine/TLS mode while the push runs another and
                # Docker attempts HTTPS against the plain-HTTP loopback registry
                # (ENG-5719 follow-up).
                insecure=insecure,
                verbose=verbose,
                target_refs=push_ref_map,
                engine=push_engine,
            )
        except ImagePushError as exc:
            console.print(f"\n[red]Error:[/red] {exc}")
            console.print(
                "  Run: [bold]kz-ext doctor[/bold] to check connection and registry access."
            )
            raise typer.Exit(code=1) from exc
        console.print()
    elif no_push:
        console.print("[dim]Skipping push (--no-push)[/dim]\n")

    # 8. Build API payload
    payload_builder = PayloadBuilder()
    from kamiwaza_extensions.constants import extract_user_id

    dev_name = PayloadBuilder.make_dev_name(
        info.name, user_id=extract_user_id(token.access_token)
    )
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
                # detect when service-filter / sdk-repo / registry /
                # push_registry / image_basename differ (review
                # re-re-re-review PR #84 H1).
                service=service,
                sdk_repo=sdk_repo,
                registry=registry,
                push_registry=push_registry,
                image_basename=info.image_basename,
                build_engine=build_engine if step == "build" else "",
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
        from kamiwaza_sdk.schemas.extensions import PatchExtension

        try:
            # Check if extension already exists
            client.extensions.get_extension(dev_name)

            # Build patch from payload — extract image, env, replicas
            # plus the x-kamiwaza per-service overrides forwarded via
            # ``extra="allow"`` (jxstanford PR #97 review H2).
            patch_services = _build_patch_service_specs(payload)
            # Carries the deployer/revision/deployed-at annotations on
            # every PATCH so `kz-ext status` reflects the current
            # redeploy (review re-review PR #84 H1).
            patch = PatchExtension(**_build_patch_kwargs(patch_services, payload))

            try:
                ext = client.extensions.patch_extension(dev_name, patch)
                console.print(
                    "  [green]\u2713[/green] Extension updated (zero-downtime)"
                )
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
            console.print(
                "[yellow]Warning:[/yellow] Invalid KAMIWAZA_DEV_TIMEOUT, using 300s"
            )
            timeout = 300
        poller = DeploymentPoller()
        try:
            ext = poller.wait_for_ready(client, dev_name, timeout=timeout)
        except DeploymentTimeoutError as exc:
            # P9 (ENG-3887): print the dev-suffixed name even on timeout so
            # the user can locate the partial deployment via kz-ext status.
            console.print(f"\n[bold]Deployment name:[/bold] {dev_name}")
            from kamiwaza_extensions.constants import EXTENSIONS_NAMESPACE
            from kamiwaza_extensions.dev_diagnostics import diagnose_dev_timeout
            from kamiwaza_extensions.exit_codes import ExitCode

            diagnosis = diagnose_dev_timeout(
                dev_name,
                EXTENSIONS_NAMESPACE,
                connection_url=connection.url,
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
        console.print(
            f"[bold]{info.name}[/bold] is running as [bold]{dev_name}[/bold] at:"
        )
        if url:
            console.print(f"  [blue]{url}[/blue]")
        else:
            console.print(
                "  [dim](no external URL reported — check kz-ext status)[/dim]"
            )
