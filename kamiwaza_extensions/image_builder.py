"""Docker image build subprocess management."""

from __future__ import annotations

import subprocess
import sys
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

        Returns:
            List of image references that were built.
        """
        services = compose_data.get("services") or {}
        built: List[str] = []

        for svc_name, svc in services.items():
            if service_filter and svc_name != service_filter:
                continue
            if "build" not in svc:
                continue

            image_ref = f"{registry}/{extension_name}-{svc_name}:{revision_tag}"
            dockerfile, context = self._resolve_build_config(svc["build"], extension_dir)

            console.print(f"  Building [bold]{svc_name}[/bold]...")
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
                dockerfile = extension_dir / df
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
            "-t", image_ref,
            "-f", str(dockerfile),
            str(context),
        ]
        try:
            if verbose:
                subprocess.run(cmd, check=True)
            else:
                result = subprocess.run(cmd, capture_output=True, text=True)
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
