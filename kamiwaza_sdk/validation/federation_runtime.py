"""Runtime adapters for the SDK-owned shared-IdP provider."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from kamiwaza_sdk.validation.federation_spec import SHARED_REALM_ADMIN_PASSWORD_REF
from kamiwaza_sdk.validation.models import RuntimeCluster, RuntimeContext


class FederationAdmin(Protocol):
    """Small Keycloak-admin surface required by the provider."""

    def create_owned_realm(self, realm: str, owner_nonce: str) -> dict[str, Any]: ...
    def delete_owned_realm(self, realm: str, owner_nonce: str) -> bool: ...
    def set_unmanaged_attributes(
        self, realm: str, *, policy: str = "ENABLED"
    ) -> None: ...
    def ensure_ropc_client(self, realm: str, client_id: str) -> dict[str, Any]: ...
    def ensure_attribute_mapper(
        self, realm: str, client_uuid: str, *, attribute: str
    ) -> None: ...
    def ensure_user(
        self,
        realm: str,
        username: str,
        *,
        password: str,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
    def delete_user(self, realm: str, user_id: str) -> bool: ...
    def delete_client(self, realm: str, client_uuid: str) -> bool: ...
    def ropc_token(
        self, realm: str, client_id: str, username: str, password: str
    ) -> str: ...


class FederationCluster(Protocol):
    """Product client wrapper; concrete clients are intentionally opaque."""

    client: Any

    def close(self) -> None: ...


ClusterFactory = Callable[[RuntimeCluster], FederationCluster]
AdminFactory = Callable[[RuntimeContext, RuntimeCluster], FederationAdmin]


def read_file_reference(reference: str, *, label: str) -> str:
    """Read a materialized file reference without accepting inline secrets."""

    parsed = urlsplit(reference)
    if parsed.scheme != "file" or parsed.netloc or not parsed.path:
        raise RuntimeError(f"{label} must be materialized as a local file")
    path = Path(unquote(parsed.path))
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError(f"{label} file is unavailable")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise RuntimeError(f"{label} file is unavailable") from None
    if not value:
        raise RuntimeError(f"{label} file is empty")
    return value


class SdkFederationCluster:
    """Concrete Kamiwaza API client for one runtime cluster."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def close(self) -> None:
        self.client.close()


class SdkFederationClusterFactory:
    """Materialize runtime API-key files into authenticated SDK clients."""

    def __init__(self, client_builder: Callable[[str, str], Any] | None = None) -> None:
        self._client_builder = client_builder or _build_client

    def __call__(self, runtime_cluster: RuntimeCluster) -> SdkFederationCluster:
        api_key = read_file_reference(
            runtime_cluster.api_key_ref, label="runtime API key"
        )
        return SdkFederationCluster(
            self._client_builder(runtime_cluster.base_url, api_key)
        )


class KeycloakAdminFactory:
    """Build a Keycloak admin client from a configured URL and secret reference.

    The admin URL is a non-secret deployment setting supplied in the process
    environment.  The password itself must be a runtime ``file://`` reference;
    accepting a raw environment value here would make it too easy to leak into
    process diagnostics or a provider artifact.
    """

    def __init__(
        self,
        admin_url: str | None = None,
        *,
        admin_user: str | None = None,
        verify: bool | None = None,
    ) -> None:
        self._admin_url = (
            admin_url or os.environ.get("KAMIWAZA_SHARED_IDP_ADMIN_URL", "")
        ).strip()
        self._admin_user = (
            admin_user or os.environ.get("KAMIWAZA_SHARED_IDP_ADMIN_USER", "admin")
        ).strip()
        self._verify = verify

    def __call__(
        self, runtime: RuntimeContext, runtime_cluster: RuntimeCluster
    ) -> FederationAdmin:
        del runtime_cluster
        if not self._admin_url:
            raise RuntimeError("shared-IdP admin URL is not configured")
        password_ref = runtime.secret_refs.get(SHARED_REALM_ADMIN_PASSWORD_REF)
        if not password_ref:
            raise RuntimeError("shared-IdP admin password reference is missing")
        password = read_file_reference(password_ref, label="shared-IdP admin password")
        from kamiwaza_sdk.seeding.federation.keycloak import KeycloakAdmin

        return KeycloakAdmin(
            self._admin_url,
            admin_user=self._admin_user,
            admin_password=password,
            verify=True if self._verify is None else self._verify,
        )


def _build_client(base_url: str, api_key: str) -> Any:
    from kamiwaza_sdk import KamiwazaClient

    return KamiwazaClient(base_url=base_url, api_key=api_key)
