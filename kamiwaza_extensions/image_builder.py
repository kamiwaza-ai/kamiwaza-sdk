"""Docker image build subprocess management."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from kamiwaza_extensions.compose_transformer import _fallback_image_basename

console = Console(stderr=True)


class ImageBuildError(RuntimeError):
    """A Docker build failed."""

    pass


class ImageBuilder:
    """Build Docker images for extension services."""

    def build(
        self,
        extension_dir: Path,
        compose_data: Dict[str, Any],
        extension_name: str,
        revision_tag: str,
        registry: str,
        service_filter: Optional[str] = None,
        verbose: bool = False,
        build_overrides: Optional[List[Any]] = None,
        image_refs: Optional[Dict[str, str]] = None,
        image_basename: Optional[str] = None,
    ) -> List[str]:
        """Build images and return the list of image references.

        Args:
            extension_dir: Root directory of the extension.
            compose_data: *Original* (untransformed) compose dict.
            extension_name: Extension name from kamiwaza.json.
            revision_tag: Tag for built images.
            registry: Registry prefix (e.g. ``registry.kamiwaza.test``).
            service_filter: Build only this service (``--service`` flag).
            verbose: Stream full build output.
            build_overrides: Optional list of ``BuildOverride`` objects from
                ``sdk_override.generate_build_overrides()``.
            image_refs: Optional per-service canonical image refs keyed by
                service name. When provided, services found in the map are
                built at the supplied ref; services not in the map fall
                back to the legacy ``{registry}/{basename}-{svc}:{tag}``
                form. Callers that own canonical-ref derivation
                (run_publish) pass this so build/push and catalog-write
                stay in lockstep.
            image_basename: Optional override for the ``{basename}``
                segment in the legacy fallback form. When None, falls
                back to ``extension_name``.

        Returns:
            List of image references that were built.
        """
        # Index overrides by service name
        override_map: Dict[str, Any] = {}
        if build_overrides:
            for bo in build_overrides:
                override_map[bo.service_name] = bo

        services = compose_data.get("services") or {}
        built: List[str] = []
        basename = _fallback_image_basename(
            extension_name,
            fallback_image_basename=image_basename,
        )

        for svc_name, svc in services.items():
            if service_filter and svc_name != service_filter:
                continue
            if "build" not in svc:
                continue

            if image_refs is not None and svc_name in image_refs:
                image_ref = image_refs[svc_name]
            else:
                image_ref = f"{registry}/{basename}-{svc_name}:{revision_tag}"
            dockerfile, context = self._resolve_build_config(
                svc["build"], extension_dir
            )

            override = override_map.get(svc_name)
            sdk_label = " (with local SDK libs)" if override else ""
            console.print(f"  Building [bold]{svc_name}[/bold]{sdk_label}...")

            if override:
                self._docker_build_with_override(
                    image_ref,
                    dockerfile,
                    context,
                    override,
                    verbose=verbose,
                )
            else:
                self._docker_build(image_ref, dockerfile, context, verbose=verbose)
            built.append(image_ref)
            console.print(f"  [green]\u2713[/green] {svc_name}  ({image_ref})")

        if service_filter and not built:
            raise ImageBuildError(
                f"Service '{service_filter}' not found or has no build context."
            )

        return built

    @staticmethod
    def _resolve_build_config(
        build_spec: Any, extension_dir: Path
    ) -> tuple[Path, Path]:
        """Return ``(dockerfile_path, context_dir)`` from a compose ``build:`` value."""
        if isinstance(build_spec, str):
            context = extension_dir / build_spec
            dockerfile = context / "Dockerfile"
        elif isinstance(build_spec, dict):
            ctx = build_spec.get("context", ".")
            context = extension_dir / ctx
            df = build_spec.get("dockerfile", "Dockerfile")
            if Path(df).is_absolute():
                dockerfile = Path(df)
            else:
                dockerfile = context / df
        else:
            context = extension_dir
            dockerfile = extension_dir / "Dockerfile"
        return dockerfile, context

    @staticmethod
    def _docker_build(
        image_ref: str,
        dockerfile: Path,
        context: Path,
        *,
        verbose: bool = False,
    ) -> None:
        cmd = [
            "docker",
            "build",
            "--load",
            "-t",
            image_ref,
            "-f",
            str(dockerfile),
            str(context),
        ]
        try:
            if verbose:
                subprocess.run(cmd, check=True, timeout=3600)
            else:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=3600
                )
                if result.returncode != 0:
                    # Show last 20 lines of output on failure
                    lines = (result.stdout + result.stderr).strip().splitlines()
                    tail = "\n".join(lines[-20:])
                    raise ImageBuildError(
                        f"Docker build failed for {image_ref}:\n{tail}"
                    )
        except FileNotFoundError:
            raise ImageBuildError(
                "Docker not found. Install Docker Desktop or ensure 'docker' is on PATH."
            )

    @staticmethod
    def _docker_build_with_override(
        image_ref: str,
        dockerfile: Path,
        context: Path,
        override: Any,
        *,
        verbose: bool = False,
    ) -> None:
        """Build with SDK override by appending overlay steps to the Dockerfile.

        Reads the original Dockerfile, appends the SDK install commands, writes
        to a temp file, and builds with ``--build-context sdk=<path>`` so the
        COPY --from=sdk directives resolve.  Single build — no two-stage FROM.
        """
        patched_file = None
        try:
            # Read original Dockerfile and apply SDK overlay
            from kamiwaza_extensions.sdk_override import apply_build_overlay

            original_content = dockerfile.read_text()
            patched_content = apply_build_overlay(original_content, override)

            fd = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".Dockerfile",
                prefix="kz-sdk-",
                delete=False,
            )
            fd.write(patched_content)
            fd.close()
            patched_file = fd.name

            cmd = [
                "docker",
                "build",
                "--load",
                "-t",
                image_ref,
                "-f",
                patched_file,
            ]
            for ctx_name, ctx_path in override.additional_build_contexts.items():
                cmd.extend(["--build-context", f"{ctx_name}={ctx_path}"])
            cmd.append(str(context))

            env = {**os.environ, "DOCKER_BUILDKIT": "1"}
            try:
                if verbose:
                    subprocess.run(cmd, check=True, timeout=3600, env=env)
                else:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=3600,
                        env=env,
                    )
                    if result.returncode != 0:
                        lines = (result.stdout + result.stderr).strip().splitlines()
                        tail = "\n".join(lines[-20:])
                        raise ImageBuildError(
                            f"Docker build (SDK override) failed for {image_ref}:\n{tail}"
                        )
            except FileNotFoundError:
                raise ImageBuildError(
                    "Docker not found. Install Docker Desktop or ensure 'docker' is on PATH."
                )
        finally:
            if patched_file:
                try:
                    os.unlink(patched_file)
                except OSError:
                    pass
