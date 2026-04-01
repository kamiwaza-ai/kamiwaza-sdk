"""Poll extension deployment status until ready or timeout."""

from __future__ import annotations

import time
from typing import Optional

from rich.console import Console

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.schemas.extensions import Extension

console = Console(stderr=True)


class DeploymentTimeoutError(RuntimeError):
    """Extension did not reach Running state within the timeout."""

    pass


class DeploymentFailedError(RuntimeError):
    """Extension reached a Failed state."""

    pass


class DeploymentPoller:
    """Poll ``GET /extensions/{name}`` until the extension is running."""

    def wait_for_ready(
        self,
        client: KamiwazaClient,
        extension_name: str,
        timeout: int = 120,
        poll_interval: int = 3,
    ) -> Extension:
        """Block until the extension reaches *Running* phase.

        Args:
            client: Authenticated SDK client.
            extension_name: CR name of the extension.
            timeout: Max seconds to wait.
            poll_interval: Seconds between polls.

        Returns:
            The extension in Running state.

        Raises:
            DeploymentTimeoutError: Timeout exceeded.
            DeploymentFailedError: Extension reached Failed phase.
        """
        deadline = time.monotonic() + timeout
        last_phase: Optional[str] = None

        while time.monotonic() < deadline:
            ext = client.extensions.get_extension(extension_name)
            phase = ext.phase or "Unknown"

            if phase != last_phase:
                console.print(f"  [dim]Status: {phase}[/dim]")
                last_phase = phase

            if phase == "Running":
                return ext

            if phase == "Failed":
                msg = self._extract_failure_message(ext)
                raise DeploymentFailedError(
                    f"Extension deployment failed: {msg}"
                )

            time.sleep(poll_interval)

        raise DeploymentTimeoutError(
            f"Extension did not reach Running state within {timeout}s "
            f"(last phase: {last_phase})"
        )

    @staticmethod
    def _extract_failure_message(ext: Extension) -> str:
        for svc in ext.services:
            if svc.message:
                return svc.message
        return "no details available"
