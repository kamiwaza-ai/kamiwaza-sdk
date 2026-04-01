"""Docker image push subprocess management."""

from __future__ import annotations

import subprocess
from typing import List, Optional

from rich.console import Console

console = Console(stderr=True)


class ImagePushError(RuntimeError):
    """A Docker push failed."""

    pass


class ImagePusher:
    """Push Docker images to a container registry."""

    def push(
        self,
        image_refs: List[str],
        registry: str,
        token: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        """Push all images to the registry.

        If *token* is provided, authenticates with the registry first
        via ``docker login`` (password passed on stdin, not CLI args).
        """
        if token:
            self.login_registry(registry, token)

        for ref in image_refs:
            short = ref.rsplit("/", 1)[-1] if "/" in ref else ref
            console.print(f"  Pushing [bold]{short}[/bold]...")
            self._docker_push(ref, verbose=verbose)
            console.print(f"  [green]\u2713[/green] {short}")

    @staticmethod
    def login_registry(registry: str, token: str) -> None:
        """Authenticate with the container registry using a PAT via stdin."""
        try:
            proc = subprocess.run(
                ["docker", "login", registry, "-u", "token", "--password-stdin"],
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
                "Docker not found. Install Docker Desktop or ensure 'docker' is on PATH."
            )

    @staticmethod
    def _docker_push(image_ref: str, *, verbose: bool = False) -> None:
        try:
            if verbose:
                subprocess.run(["docker", "push", image_ref], check=True)
            else:
                result = subprocess.run(
                    ["docker", "push", image_ref],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    raise ImagePushError(
                        f"Push failed for {image_ref}: {result.stderr.strip()}"
                    )
        except FileNotFoundError:
            raise ImagePushError(
                "Docker not found. Install Docker Desktop or ensure 'docker' is on PATH."
            )
