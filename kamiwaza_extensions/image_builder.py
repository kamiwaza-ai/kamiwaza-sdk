"""Docker image build subprocess management."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

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

        for svc_name, svc in services.items():
            if service_filter and svc_name != service_filter:
                continue
            if "build" not in svc:
                continue

            image_ref = f"{registry}/{extension_name}-{svc_name}:{revision_tag}"
            dockerfile, context = self._resolve_build_config(svc["build"], extension_dir)

            override = override_map.get(svc_name)
            sdk_label = " (with local SDK libs)" if override else ""
            console.print(f"  Building [bold]{svc_name}[/bold]{sdk_label}...")

            if override:
                self._docker_build_with_override(
                    image_ref, dockerfile, context, override, verbose=verbose,
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
            "docker", "build",
            "--load",
            "-t", image_ref,
            "-f", str(dockerfile),
            str(context),
        ]
        try:
            if verbose:
                subprocess.run(cmd, check=True, timeout=3600)
            else:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
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
        """Build with SDK override: build the base image, then apply a wrapper.

        1. Build the original Dockerfile to a temporary base tag.
        2. Write the wrapper Dockerfile to a temp file.
        3. Build the wrapper with ``--build-arg BASE_IMAGE=<base>`` and
           ``--build-context sdk=<path>`` to overlay the local SDK lib.
        """
        base_tag = f"{image_ref}-base"
        wrapper_file = None

        try:
            # Step 1: Build the original image as base
            base_cmd = [
                "docker", "build", "--load",
                "-t", base_tag,
                "-f", str(dockerfile),
                str(context),
            ]
            env = {**os.environ, "DOCKER_BUILDKIT": "1"}

            if verbose:
                subprocess.run(base_cmd, check=True, timeout=3600, env=env)
            else:
                result = subprocess.run(
                    base_cmd, capture_output=True, text=True, timeout=3600, env=env,
                )
                if result.returncode != 0:
                    lines = (result.stdout + result.stderr).strip().splitlines()
                    tail = "\n".join(lines[-20:])
                    raise ImageBuildError(
                        f"Docker build (base) failed for {image_ref}:\n{tail}"
                    )

            # Step 2: Write wrapper Dockerfile
            fd = tempfile.NamedTemporaryFile(
                mode="w", suffix=".Dockerfile", prefix="kz-sdk-wrapper-", delete=False,
            )
            fd.write(override.wrapper_dockerfile_content)
            fd.close()
            wrapper_file = fd.name

            # Step 3: Build wrapper with build contexts
            wrapper_cmd = [
                "docker", "build", "--load",
                "-t", image_ref,
                "-f", wrapper_file,
                "--build-arg", f"BASE_IMAGE={base_tag}",
            ]
            for ctx_name, ctx_path in override.additional_build_contexts.items():
                wrapper_cmd.extend(["--build-context", f"{ctx_name}={ctx_path}"])
            # Use a minimal context (the wrapper doesn't need the source)
            wrapper_cmd.append(context.anchor if hasattr(context, "anchor") else "/")

            if verbose:
                subprocess.run(wrapper_cmd, check=True, timeout=3600, env=env)
            else:
                result = subprocess.run(
                    wrapper_cmd, capture_output=True, text=True, timeout=3600, env=env,
                )
                if result.returncode != 0:
                    lines = (result.stdout + result.stderr).strip().splitlines()
                    tail = "\n".join(lines[-20:])
                    raise ImageBuildError(
                        f"Docker build (SDK wrapper) failed for {image_ref}:\n{tail}"
                    )

        except FileNotFoundError:
            raise ImageBuildError(
                "Docker not found. Install Docker Desktop or ensure 'docker' is on PATH."
            )
        finally:
            if wrapper_file:
                try:
                    os.unlink(wrapper_file)
                except OSError:
                    pass
