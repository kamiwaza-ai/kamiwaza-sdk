"""Poll extension deployment status until ready or timeout."""

from __future__ import annotations

import subprocess
import time
from typing import Optional

from rich.console import Console

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.schemas.extensions import Extension, ExtensionStatus

from kamiwaza_extensions.constants import EXTENSIONS_NAMESPACE as _EXTENSIONS_NS

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
        """Block until the extension reaches *Running* phase and pods are ready.

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
        last_ready: Optional[str] = None
        use_status_endpoint = True

        while time.monotonic() < deadline:
            ext = client.extensions.get_extension(extension_name)
            phase = ext.phase or "Unknown"

            if phase != last_phase:
                console.print(f"  [dim]Status: {phase}[/dim]")
                last_phase = phase

            if phase == "Failed":
                msg = self._extract_failure_message(ext)
                raise DeploymentFailedError(
                    f"Extension deployment failed: {msg}"
                )

            if phase == "Running":
                # Try status endpoint for richer readiness info
                if use_status_endpoint:
                    ready, summary = self._check_via_status_endpoint(
                        client, extension_name
                    )
                    if ready is not None:
                        if summary != last_ready:
                            console.print(f"  [dim]Pods: {summary}[/dim]")
                            last_ready = summary
                        if ready:
                            return ext
                        time.sleep(poll_interval)
                        continue
                    # Status endpoint not available — disable and fall through
                    use_status_endpoint = False

                # Fallback: verify pods via kubectl
                ready, summary = self._check_pods_ready(extension_name)
                if summary != last_ready:
                    console.print(f"  [dim]Pods: {summary}[/dim]")
                    last_ready = summary
                if ready:
                    return ext

            time.sleep(poll_interval)

        raise DeploymentTimeoutError(
            f"Extension did not reach Running state within {timeout}s "
            f"(last phase: {last_phase})"
        )

    @staticmethod
    def _check_via_status_endpoint(
        client: KamiwazaClient, extension_name: str
    ) -> tuple[Optional[bool], str]:
        """Try the /status endpoint for per-service readiness.

        Returns ``(None, "")`` if the endpoint is not available (404/405),
        otherwise ``(all_ready, summary_string)``.
        """
        from kamiwaza_sdk.exceptions import APIError

        try:
            status: ExtensionStatus = client.extensions.get_extension_status(
                extension_name
            )
        except APIError as exc:
            if exc.status_code in (404, 405, 501):
                # Status endpoint not available — caller will fall back
                return None, ""
            # Auth errors, server errors — log and fall back
            console.print(f"[dim]Status endpoint error: {exc}[/dim]")
            return None, ""
        except Exception:
            # Network errors etc. — fall back silently
            return None, ""

        if status.rolling_update:
            parts = [
                f"{svc.name} {svc.ready_replicas}/{svc.replicas} ready"
                for svc in status.services
            ]
            return False, "Rolling out... " + ", ".join(parts)

        total_ready = sum(svc.ready_replicas for svc in status.services)
        total_desired = sum(svc.replicas for svc in status.services)
        all_ready = total_ready == total_desired and total_desired > 0

        parts = [
            f"{svc.name} {svc.ready_replicas}/{svc.replicas} ready"
            for svc in status.services
        ]
        return all_ready, ", ".join(parts) if parts else "no services"

    @staticmethod
    def _check_pods_ready(extension_name: str) -> tuple[bool, str]:
        """Check if all pods for the extension are ready via kubectl."""
        try:
            result = subprocess.run(
                [
                    "kubectl", "get", "pods",
                    "-n", _EXTENSIONS_NS,
                    "-l", f"extensions.kamiwaza.io/deployment-id={extension_name}",
                    "-o", "jsonpath={range .items[*]}{.status.containerStatuses[0].ready}{' '}{end}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False, "checking..."

            statuses = result.stdout.strip().split()
            if not statuses:
                return False, "no pods yet"

            ready_count = sum(1 for s in statuses if s == "true")
            total = len(statuses)
            summary = f"{ready_count}/{total} ready"
            return ready_count == total, summary
        except FileNotFoundError:
            # kubectl not installed — warn and skip pod-readiness gate
            console.print("  [yellow]Warning:[/yellow] kubectl not found, cannot verify pod readiness")
            return True, "skipped (no kubectl)"
        except subprocess.TimeoutExpired:
            return False, "checking... (kubectl timeout)"

    @staticmethod
    def _extract_failure_message(ext: Extension) -> str:
        for svc in ext.services:
            if svc.message:
                return svc.message
        return "no details available"
