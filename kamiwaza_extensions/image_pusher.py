"""Docker/Podman image push subprocess management."""

from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional

from rich.console import Console

console = Console(stderr=True)


class ImagePushError(RuntimeError):
    """A Docker push failed."""

    pass


def _has_podman() -> bool:
    """Return True if the ``podman`` CLI is available on PATH."""
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
    ) -> None:
        """Push all images to the registry.

        If *token* is provided, authenticates with the registry first.
        When *insecure* is True and Podman is available, ``--tls-verify=false``
        is passed to bypass self-signed certificate errors.
        """
        use_podman = insecure and _has_podman()
        if token:
            self._login_registry(registry, token, use_podman=use_podman)

        for ref in image_refs:
            short = ref.rsplit("/", 1)[-1] if "/" in ref else ref
            console.print(f"  Pushing [bold]{short}[/bold]...")
            self._push(ref, use_podman=use_podman, verbose=verbose)
            console.print(f"  [green]\u2713[/green] {short}")

    @staticmethod
    def _login_registry(registry: str, token: str, *, use_podman: bool = False) -> None:
        """Authenticate with the container registry using a PAT via stdin."""
        cli = "podman" if use_podman else "docker"
        cmd = [cli, "login", registry, "-u", "token", "--password-stdin"]
        if use_podman:
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

    @staticmethod
    def _push(image_ref: str, *, use_podman: bool = False, verbose: bool = False) -> None:
        cli = "podman" if use_podman else "docker"
        cmd = [cli, "push", image_ref]
        if use_podman:
            cmd.insert(2, "--tls-verify=false")
        try:
            if verbose:
                subprocess.run(cmd, check=True)
            else:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    raise ImagePushError(
                        f"Push failed for {image_ref}: {result.stderr.strip()}"
                    )
        except FileNotFoundError:
            raise ImagePushError(
                f"{cli} not found. Install Docker/Podman or ensure '{cli}' is on PATH."
            )
