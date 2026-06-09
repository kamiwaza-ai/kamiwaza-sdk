"""Docker/Podman image push subprocess management."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Dict, List, Optional

from rich.console import Console

console = Console(stderr=True)


# OCI image digest grammar: sha256: followed by 64 lowercase hex chars.
# Used both for validating user-supplied --digest input and for sanity-
# checking the output of `docker buildx imagetools inspect`.
DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")


def validate_digest(digest: str) -> None:
    """Raise ``ValueError`` if *digest* is not a ``sha256:<64-hex>`` string.

    Phrasing avoids square brackets in the message — rich console markup
    treats ``[a-f0-9]`` as a (broken) tag and silently strips it from
    user-facing output.
    """
    if not DIGEST_PATTERN.match(digest):
        raise ValueError(
            f"Invalid digest '{digest}': must be 'sha256:' followed by "
            "64 lowercase hex characters"
        )


class ImagePushError(RuntimeError):
    """A Docker push failed."""

    pass


def _has_podman() -> bool:
    """Return True if the ``podman`` CLI is available on PATH.

    Same predicate as ``registry_resolution._has_podman``; both feed
    ``select_push_engine``, which is the single source of truth for
    *engine selection*. The duplication avoids an import cycle on the
    hot push path. If the selection rule changes, update
    ``select_push_engine`` and every caller listed there.
    """
    return shutil.which("podman") is not None


class ImagePusher:
    """Push Docker images to a container registry."""

    def push(
        self,
        image_refs: List[str],
        registry: str,
        token: Optional[str] = None,
        insecure: bool = False,
        verbose: bool = False,
        target_refs: Optional[Dict[str, str]] = None,
        engine: Optional[str] = None,
    ) -> None:
        """Push all images to the registry.

        If *token* is provided, authenticates with the registry first.
        When *insecure* is True and Podman is used, ``--tls-verify=false`` is
        passed to bypass self-signed certificate errors.

        ``target_refs`` optionally maps built/deployment refs to alternate
        push refs. This supports local topologies where the same registry is
        reachable under different hostnames from the build VM and the cluster.

        ``engine`` can force the push binary to ``"docker"`` or ``"podman"``.
        When omitted, the historical auto-selection behavior is preserved.
        """
        if engine is None:
            # Must mirror ``registry_resolution.select_push_engine``: that
            # helper is what callers use to gate insecure-registries /
            # engine-mismatch pre-flight checks. Changing this rule without
            # updating ``select_push_engine`` will desync the pre-flight from
            # the actual push behavior.
            from kamiwaza_extensions.registry_resolution import podman_push_available

            use_podman = insecure and podman_push_available()
        else:
            normalized_engine = engine.lower()
            if normalized_engine not in ("docker", "podman"):
                raise ImagePushError(
                    f"Unsupported push engine '{engine}'; expected 'docker' or 'podman'"
                )
            use_podman = normalized_engine == "podman"
        if token:
            self._login_registry(
                registry,
                token,
                use_podman=use_podman,
                insecure=insecure,
            )

        for ref in image_refs:
            push_ref = (target_refs or {}).get(ref, ref)
            short = push_ref.rsplit("/", 1)[-1] if "/" in push_ref else push_ref
            console.print(f"  Pushing [bold]{short}[/bold]...")
            if push_ref != ref:
                self._tag(ref, push_ref, use_podman=use_podman, verbose=verbose)
            self._push(
                push_ref,
                use_podman=use_podman,
                insecure=insecure,
                verbose=verbose,
            )
            console.print(f"  [green]\u2713[/green] {short}")

    @staticmethod
    def _login_registry(
        registry: str,
        token: str,
        *,
        use_podman: bool = False,
        insecure: bool = False,
    ) -> None:
        """Authenticate with the container registry using a PAT via stdin."""
        cli = "podman" if use_podman else "docker"
        cmd = [cli, "login", registry, "-u", "token", "--password-stdin"]
        if use_podman and insecure:
            cmd.insert(2, "--tls-verify=false")
        try:
            proc = subprocess.run(
                cmd,
                input=token,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                raise ImagePushError(
                    f"Registry login failed for {registry}: {proc.stderr.strip()}"
                )
        except FileNotFoundError:
            raise ImagePushError(
                f"{cli} not found. Install Docker/Podman or ensure '{cli}' is on PATH."
            )
        except subprocess.TimeoutExpired as exc:
            raise ImagePushError(
                f"Registry login to {registry} timed out after 30s"
            ) from exc

    @staticmethod
    def check_buildx_available() -> None:
        """Verify ``docker buildx imagetools`` is usable on PATH.

        Run before push when a downstream ``resolve_digest`` call will
        be required, so a missing buildx plugin fails the publish *before*
        the registry is mutated rather than after. Modern docker (Desktop,
        docker-ce 20.10+) bundles buildx; this guards against older or
        minimal installations where the plugin is absent.

        Raises:
            ImagePushError: When ``docker`` is not installed or the
                ``buildx imagetools`` subcommand isn't recognized.
        """
        try:
            result = subprocess.run(
                ["docker", "buildx", "imagetools", "--help"],
                capture_output=True,
                timeout=5,
            )
        except FileNotFoundError as exc:
            raise ImagePushError(
                "docker not found — required for digest pinning. "
                "Install Docker (with buildx) before publishing."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ImagePushError(
                "docker buildx availability check timed out after 5s"
            ) from exc
        if result.returncode != 0:
            raise ImagePushError(
                "docker buildx imagetools is not available — required "
                "for digest pinning. Modern docker (20.10+) bundles "
                "buildx; older installs may need the plugin added."
            )

    @staticmethod
    def resolve_digest(image_ref: str) -> str:
        """Return the manifest digest for *image_ref* from the registry.

        Uses ``docker buildx imagetools inspect`` with a JSON template
        so that an OCI manifest list (multi-arch) returns its index
        digest and a single-platform manifest returns its own digest —
        matching what ``image:tag@sha256:...`` resolves to at pull
        time. The ref must already exist in the registry; call after
        ``push`` for the just-built image, or against any pre-existing
        tag.

        Note: ``{{.Manifest.Digest}}`` does not work — that template
        path hits the type's Stringer and dumps a human-readable
        manifest. The JSON form is the canonical extraction.

        Raises:
            ImagePushError: When ``docker`` is missing, the inspect
                command fails, the JSON cannot be parsed, or the
                returned digest does not match the expected grammar.
        """
        import json as _json

        cmd = [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            image_ref,
            "--format",
            "{{json .Manifest}}",
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise ImagePushError(
                "docker not found. Install Docker (with buildx) to resolve image digests."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ImagePushError(
                f"Digest resolution for {image_ref} timed out after 60s"
            ) from exc
        if result.returncode != 0:
            raise ImagePushError(
                f"Digest resolution failed for {image_ref}: {result.stderr.strip()}"
            )
        try:
            manifest = _json.loads(result.stdout)
        except _json.JSONDecodeError as exc:
            raise ImagePushError(
                f"Could not parse imagetools output for {image_ref}: {exc}"
            ) from exc
        # Guard against valid-but-unexpected JSON shapes (lists, scalars).
        # `imagetools inspect --format '{{json .Manifest}}'` returns an OCI
        # Descriptor object, but a registry/buildx quirk could surface
        # something else; bare .get() would raise AttributeError.
        if not isinstance(manifest, dict):
            raise ImagePushError(
                f"Unexpected imagetools output for {image_ref}: "
                f"expected an object, got {type(manifest).__name__}"
            )
        digest = manifest.get("digest")
        if not isinstance(digest, str) or not DIGEST_PATTERN.match(digest):
            raise ImagePushError(f"Unexpected digest field for {image_ref}: {digest!r}")
        return digest

    @staticmethod
    def _push(
        image_ref: str,
        *,
        use_podman: bool = False,
        insecure: bool = False,
        verbose: bool = False,
    ) -> None:
        cli = "podman" if use_podman else "docker"
        cmd = [cli, "push", image_ref]
        if use_podman and insecure:
            cmd.insert(2, "--tls-verify=false")
        try:
            if verbose:
                subprocess.run(cmd, check=True, timeout=600)
            else:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                if result.returncode != 0:
                    raise ImagePushError(
                        f"Push failed for {image_ref}: {result.stderr.strip()}"
                    )
        except FileNotFoundError:
            raise ImagePushError(
                f"{cli} not found. Install Docker/Podman or ensure '{cli}' is on PATH."
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or str(exc)).strip()
            raise ImagePushError(f"Push failed for {image_ref}: {detail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ImagePushError(f"Push timed out after 600s for {image_ref}") from exc

    @staticmethod
    def _tag(
        source_ref: str,
        target_ref: str,
        *,
        use_podman: bool = False,
        verbose: bool = False,
    ) -> None:
        cli = "podman" if use_podman else "docker"
        cmd = [cli, "tag", source_ref, target_ref]
        try:
            if verbose:
                subprocess.run(cmd, check=True, timeout=120)
            else:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    raise ImagePushError(
                        f"Retag failed for {source_ref} -> {target_ref}: "
                        f"{result.stderr.strip()}"
                    )
        except FileNotFoundError:
            raise ImagePushError(
                f"{cli} not found. Install Docker/Podman or ensure '{cli}' is on PATH."
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or str(exc)).strip()
            raise ImagePushError(
                f"Retag failed for {source_ref} -> {target_ref}: {detail}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ImagePushError(
                f"Retag timed out after 120s for {source_ref} -> {target_ref}"
            ) from exc
