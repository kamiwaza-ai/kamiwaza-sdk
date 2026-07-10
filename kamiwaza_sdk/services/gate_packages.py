"""T7.10 / ENG-4765 — Gate-package SDK service (WS-M5).

Customer surface: ``kz.gates.packages.{install, replace, list, get, uninstall}``.

Exposed via the ``GatesAPI.packages`` lazy property — ``kz.gates`` is the
discovery surface (M2 / T5.4); ``kz.gates.packages`` is the install
surface (M5). Two semantically related capabilities live under a single
top-level service per the design's "code-level sub-API on kz.gates"
framing.

Server-side correlate: ``kamiwaza/services/authz/gate_packages/api.py``
(T7.2 / ENG-4757). All five SDK methods translate 1:1 to the same HTTP
verb on ``/api/authz/gate-packages``. M5a ships install + list + get;
M5b ships replace + uninstall.
"""

from __future__ import annotations

from typing import Optional

from ..schemas.gate_packages import (
    GatePackageInstallResult,
    GatePackageList,
    GatePackageSpec,
    GatePackageState,
)
from .base_service import BaseService


class GatePackagesAPI(BaseService):
    """Install, replace, list, get, and uninstall gate packages."""

    def install(
        self,
        package_spec: str,
        hash_digest: str,
        *,
        index_url: Optional[str] = None,
    ) -> GatePackageInstallResult:
        """Install a gate package (FR-89 / FR-95 / WS-M5a).

        Args:
            package_spec: Version-pinned pip spec, e.g.
                ``"acme-gates==1.2.3"``. Unpinned specs are rejected.
            hash_digest: SHA-256 of the wheel as published on the
                index, e.g. ``"sha256:abcd..."``. REQUIRED at MVP.
            index_url: Optional override for the chart-configured pip
                index. Server enforces the chart-configured allowlist.

        Returns:
            ``GatePackageInstallResult`` with the new state row +
            install duration + audit event id.

        Raises:
            GatePackageHashRequiredError: 400 when ``hash_digest`` is
                missing (Pydantic + server-side double check).
            GatePackageHashMismatchError: 422 when pip --require-hashes
                rejects the wheel.
            GatePackageInstallTimeoutError: 504 when pip subprocess
                exceeds the chart-configured install timeout.
        """
        spec = GatePackageSpec(
            package_spec=package_spec,
            hash_digest=hash_digest,
            index_url=index_url,
        )
        response = self.client._request(
            "POST",
            "/authz/gate-packages",
            json=spec.model_dump(exclude_none=True),
        )
        return GatePackageInstallResult.model_validate(response)

    def list(self) -> GatePackageList:
        """List installed gate packages (FR-90)."""
        response = self.client._request("GET", "/authz/gate-packages")
        return GatePackageList.model_validate(response)

    def get(self, name: str) -> GatePackageState:
        """Get the state record for one installed gate package (FR-90)."""
        response = self.client._request("GET", f"/authz/gate-packages/{name}")
        return GatePackageState.model_validate(response)

    def replace(
        self,
        name: str,
        package_spec: str,
        hash_digest: str,
        *,
        index_url: Optional[str] = None,
    ) -> GatePackageInstallResult:
        """Atomic in-place replace (FR-89a). Ships in WS-M5b.

        Raises:
            GatePackageHashRequiredError: 400 when ``hash_digest`` is missing.
            GatePackageHashMismatchError: 422 when pip --require-hashes
                rejects the wheel.
            GatePackageInstallTimeoutError: 504 on pip-subprocess timeout.
            GatePackageNotFoundError: 404 when no package named ``name`` is
                currently installed.
            GatePackageClasspathDropError: 409 when the candidate package's
                classpaths are not a superset of the active package's
                classpaths. Rebinding the dropped classpaths is operator
                work; the error body names the diff.
        """
        spec = GatePackageSpec(
            package_spec=package_spec,
            hash_digest=hash_digest,
            index_url=index_url,
        )
        response = self.client._request(
            "PUT",
            f"/authz/gate-packages/{name}",
            json=spec.model_dump(exclude_none=True),
        )
        return GatePackageInstallResult.model_validate(response)

    def uninstall(self, name: str) -> None:
        """Uninstall (FR-90). Server refuses if any active binding
        references a classpath from the package. Ships in WS-M5b.

        Return value is reserved; M5b may surface the audit event id.

        Raises:
            GatePackageNotFoundError: 404 when no package named ``name``
                is currently installed.
            GatePackageUninstallBlockedError: 409 when at least one active
                binding still references a classpath from the package.
                The error body names the blocking bindings.
        """
        self.client._request("DELETE", f"/authz/gate-packages/{name}")
