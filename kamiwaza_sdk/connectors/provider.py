"""Connector provider SPI.

The contract every connector provider satisfies, keyed by ``connector_type``.
Subscribed connectors register a :class:`.providers.remote.RemoteConnectorProvider`
in :mod:`.registry`; external connectors will later be loaded by classpath and
validated with ``isinstance``, mirroring the gate-package plugin contract in
``kamiwaza/services/authz/gates/protocol.py``.

The contract grows one method at a time as cross-provider dispatch is migrated
off ``connector_type ==`` string checks; today it carries identity and config
validation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any, ClassVar

from .config_schema import ConfigSchema


@dataclass(frozen=True)
class AuthModel:
    """How a connector authenticates — base of a closed discriminated union.

    Variants are markers today, distinguished by ``kind``; per-variant manifest
    detail (oauth flow, scopes, secret fields) is added when a consumer needs
    it. ``kind`` is the stable discriminant rendered into the manifest.
    """

    kind: ClassVar[str]


@dataclass(frozen=True)
class PerUserOAuth(AuthModel):
    """Each user authorizes their own account; tokens brokered per-user.

    The model behind per-user connectors: core brokers and refreshes the user's
    token generically from the manifest's OAuth descriptor (see
    :mod:`kamiwaza.services.connectors.token_broker`), so the connector never holds it.
    """

    kind: ClassVar[str] = "per_user_oauth"


@dataclass(frozen=True)
class ServiceToken(AuthModel):
    """A single shared credential configured once; no per-user OAuth.

    The model behind service-token integrations (e.g. a bot's API key) that
    have no per-user authorization step.
    """

    kind: ClassVar[str] = "service_token"


_AUTH_MODELS: dict[str, type[AuthModel]] = {
    PerUserOAuth.kind: PerUserOAuth,
    ServiceToken.kind: ServiceToken,
}


def auth_model_from_kind(kind: str) -> AuthModel:
    """Reconstruct an :class:`AuthModel` from its manifest ``kind`` discriminant.

    Fails fast on an unknown kind rather than guessing a default — a malformed
    manifest is a contract error, not a degradation to absorb silently.
    """
    try:
        return _AUTH_MODELS[kind]()
    except KeyError:
        raise ValueError(f"Unknown auth model kind: {kind!r}") from None


@dataclass(frozen=True)
class OAuthDescriptor:
    """A connector's OAuth endpoints + scopes, declared in its manifest.

    Lets core run the OAuth authorization ceremony generically: the endpoints,
    scopes, and flow type come from the connector, so no provider OAuth specifics
    are hardcoded in core. The admin still supplies the client credentials via the
    config schema -- those are never in the manifest. ``extra_auth_params`` carries
    provider-specific authorization-URL params (e.g. an ``access_type=offline`` flag)
    as ordered pairs, so the descriptor stays frozen/hashable and JSON-stable.
    """

    token_endpoint: str
    # Optional: a device_code (RFC 8628) connector ships no authorization_endpoint
    # (only token + device_authorization), so this is None for that flow.
    authorization_endpoint: str | None = None
    revocation_endpoint: str | None = None
    # RFC 8628 device-authorization endpoint, for connectors that declare
    # ``flow="device_code"`` (public clients with no redirect, e.g. M365).
    device_authorization_endpoint: str | None = None
    scopes: tuple[str, ...] = ()
    flow: str = "authorization_code"
    extra_auth_params: tuple[tuple[str, str], ...] = ()

    def to_manifest(self) -> dict[str, Any]:
        """Serialize to a plain-JSON object for the manifest envelope."""
        return {
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "revocation_endpoint": self.revocation_endpoint,
            "device_authorization_endpoint": self.device_authorization_endpoint,
            "scopes": list(self.scopes),
            "flow": self.flow,
            "extra_auth_params": dict(self.extra_auth_params),
        }

    @classmethod
    def from_manifest(cls, data: dict[str, Any]) -> OAuthDescriptor:
        """Reconstruct a descriptor from a manifest produced by :meth:`to_manifest`."""
        return cls(
            # Optional for device_code manifests (only token + device endpoints).
            authorization_endpoint=data.get("authorization_endpoint"),
            token_endpoint=data["token_endpoint"],
            revocation_endpoint=data.get("revocation_endpoint"),
            device_authorization_endpoint=data.get("device_authorization_endpoint"),
            scopes=tuple(data.get("scopes") or ()),
            flow=data.get("flow", "authorization_code"),
            extra_auth_params=tuple(
                (str(k), str(v))
                for k, v in (data.get("extra_auth_params") or {}).items()
            ),
        )

    def resolved(self, config: dict[str, Any]) -> OAuthDescriptor:
        """Return a copy with ``{config_key}`` placeholders in the endpoints filled.

        A connector may declare config-dependent endpoints (e.g. M365's
        single-tenant authority ``.../{tenant_id}/oauth2/v2.0/token``). Resolving
        on the descriptor means *every* consumer — the authorization ceremony, the
        device-code poll, and the per-user refresh — is placeholder-safe by
        construction, instead of each call site remembering to substitute (the gap
        that left token refresh POSTing to a literal ``{tenant_id}`` URL). A no-op
        for fixed endpoints (e.g. Google).
        """

        def fill(url: str) -> str:
            for key, value in config.items():
                if isinstance(value, str):
                    url = url.replace("{" + key + "}", value)
            return url

        return replace(
            self,
            authorization_endpoint=(
                fill(self.authorization_endpoint)
                if self.authorization_endpoint
                else None
            ),
            token_endpoint=fill(self.token_endpoint),
            revocation_endpoint=(
                fill(self.revocation_endpoint) if self.revocation_endpoint else None
            ),
            device_authorization_endpoint=(
                fill(self.device_authorization_endpoint)
                if self.device_authorization_endpoint
                else None
            ),
        )


@dataclass(frozen=True)
class DeploymentDescriptor:
    """How core runs this connector as a deployed workload.

    Required for every subscribed connector: a connector runs as a deployed
    extension, so the manifest self-describes its deployable image (and an optional
    serving port) and core deploys it as a ``type: connector`` KamiwazaExtension --
    no public ingress, no external egress (it reaches its source only through core's
    proxy). The image fields mirror the platform's ImageSpec shape
    (registry / repository / tag).
    """

    image_repository: str
    image_registry: str = "ghcr.io"
    image_tag: str = "latest"
    port: int | None = None
    # Digest pin (``sha256:...``) for the deployed image. The connector's own
    # spec leaves this None; the catalog publisher (kz-ext) resolves and pins
    # the pushed image so a catalog-deployed connector is immutable.
    image_digest: str | None = None

    def to_manifest(self) -> dict[str, Any]:
        """Serialize to a plain-JSON object for the manifest envelope."""
        manifest: dict[str, Any] = {
            "image_repository": self.image_repository,
            "image_registry": self.image_registry,
            "image_tag": self.image_tag,
            "port": self.port,
        }
        # Omit unless pinned, so an unpinned connector's manifest is unchanged.
        if self.image_digest:
            manifest["image_digest"] = self.image_digest
        return manifest

    @classmethod
    def from_manifest(cls, data: dict[str, Any]) -> DeploymentDescriptor:
        """Reconstruct a descriptor from a manifest produced by :meth:`to_manifest`."""
        return cls(
            image_repository=data["image_repository"],
            image_registry=data.get("image_registry", "ghcr.io"),
            image_tag=data.get("image_tag", "latest"),
            port=data.get("port"),
            image_digest=data.get("image_digest"),
        )


@dataclass(frozen=True)
class ConstraintDescriptor:
    """A static, manifest-declared surface constraint (machine-readable).

    The connector self-describes the constraints core used to hardcode per provider
    (e.g. "search needs a drive scope", "folders import as files"). Dynamic,
    user/workroom-dependent constraints (missing-scope reauth) are still computed by
    core at catalog time -- these are the static ones the connector owns.
    """

    code: str
    message: str
    actions: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    metadata: tuple[tuple[str, Any], ...] = ()

    def to_manifest(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "actions": list(self.actions),
            "source_types": list(self.source_types),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_manifest(cls, data: dict[str, Any]) -> ConstraintDescriptor:
        return cls(
            code=data["code"],
            message=data["message"],
            actions=tuple(data.get("actions") or ()),
            source_types=tuple(data.get("source_types") or ()),
            metadata=tuple((data.get("metadata") or {}).items()),
        )


@dataclass(frozen=True)
class SurfaceDescriptor:
    """One browse/list/search surface a connector offers, fully self-described.

    Replaces the per-provider capability blocks, op-name dicts, key-alias table, and
    unsupported-import lists core used to hardcode in ``surfaces.py``. ``name`` and
    ``resource_kinds`` are free strings, so a non-file connector (issues, messages,
    tables) declares its own vocabulary with no core change -- core renders and routes
    from this data and never branches on the connector type.

    ``operations`` maps a logical action (``"list"``/``"search"``/``"content"``) to
    the connector op name core calls for it (``("list", "drive.list_items")``), so the
    op vocabulary lives in the manifest, not in core.
    """

    name: str
    display_label: str
    operations: tuple[tuple[str, str], ...] = ()
    resource_kinds: tuple[str, ...] = ()
    selection_modes: tuple[str, ...] = ()
    search_supported: bool = False
    freshness_supported: bool = False
    constraints: tuple[ConstraintDescriptor, ...] = ()
    unsupported_import_kinds: tuple[str, ...] = ()
    credential_key_aliases: tuple[str, ...] = ()

    def operation(self, action: str) -> str | None:
        """The connector op name bound to *action*, or None if unsupported."""
        for act, op in self.operations:
            if act == action:
                return op
        return None

    def to_manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_label": self.display_label,
            "operations": [[action, op] for action, op in self.operations],
            "resource_kinds": list(self.resource_kinds),
            "selection_modes": list(self.selection_modes),
            "search_supported": self.search_supported,
            "freshness_supported": self.freshness_supported,
            "constraints": [c.to_manifest() for c in self.constraints],
            "unsupported_import_kinds": list(self.unsupported_import_kinds),
            "credential_key_aliases": list(self.credential_key_aliases),
        }

    @classmethod
    def from_manifest(cls, data: dict[str, Any]) -> SurfaceDescriptor:
        return cls(
            name=data["name"],
            display_label=data.get("display_label", data["name"]),
            operations=tuple(
                (str(pair[0]), str(pair[1])) for pair in (data.get("operations") or [])
            ),
            resource_kinds=tuple(data.get("resource_kinds") or ()),
            selection_modes=tuple(data.get("selection_modes") or ()),
            search_supported=bool(data.get("search_supported", False)),
            freshness_supported=bool(data.get("freshness_supported", False)),
            constraints=tuple(
                ConstraintDescriptor.from_manifest(c)
                for c in (data.get("constraints") or [])
            ),
            unsupported_import_kinds=tuple(data.get("unsupported_import_kinds") or ()),
            credential_key_aliases=tuple(data.get("credential_key_aliases") or ()),
        )


@dataclass(frozen=True)
class ConnectorSpec:
    """A connector's self-description (the manifest the platform renders).

    Identity, auth model, and the connector's declared ``surfaces`` (browse/list/
    search capabilities, op names, resource kinds, and static constraints) -- so core
    renders the catalog and routes operations entirely from this manifest, with no
    per-provider code.

    ``egress_allowlist`` is the set of external hostnames the connector may reach
    (its SaaS endpoints, e.g. ``api.linear.app``). The platform records it as the
    connector's declared egress intent and enforces it deny-by-default at core's
    proxy (``connector_proxy_service._enforce_egress``): a connector reaches its
    source only through core, whose deployment carries no external egress of its
    own. Empty means no external egress.

    ``icon`` is the connector's display logo, carried self-contained in the manifest
    (e.g. a ``data:`` URI) so the connector ships its own branding and the platform
    only renders it -- no core-hosted asset, no hardcoded per-type map, works
    airgapped. ``None`` means the UI falls back to a generic icon.
    """

    connector_type: str
    provider_id: str
    provider_label: str
    auth_model: AuthModel = PerUserOAuth()
    egress_allowlist: tuple[str, ...] = ()
    oauth: OAuthDescriptor | None = None
    deployment: DeploymentDescriptor | None = None
    icon: str | None = None
    surfaces: tuple[SurfaceDescriptor, ...] = ()
    constraints: tuple[ConstraintDescriptor, ...] = ()

    def to_manifest(self) -> dict[str, Any]:
        """Serialize to a plain-JSON manifest the platform stores and transmits.

        ``auth_model`` nests under its own object so variants can grow fields
        (scopes, secret fields) without changing the envelope.
        """
        return {
            "connector_type": self.connector_type,
            "provider_id": self.provider_id,
            "provider_label": self.provider_label,
            "icon": self.icon,
            "auth_model": {"kind": self.auth_model.kind},
            "egress_allowlist": list(self.egress_allowlist),
            "oauth": self.oauth.to_manifest() if self.oauth else None,
            "deployment": self.deployment.to_manifest() if self.deployment else None,
            "surfaces": [surface.to_manifest() for surface in self.surfaces],
            "constraints": [c.to_manifest() for c in self.constraints],
        }

    @classmethod
    def from_manifest(cls, data: dict[str, Any]) -> ConnectorSpec:
        """Reconstruct a spec from a manifest produced by :meth:`to_manifest`."""
        return cls(
            connector_type=data["connector_type"],
            provider_id=data["provider_id"],
            provider_label=data["provider_label"],
            icon=data.get("icon"),
            auth_model=auth_model_from_kind(data["auth_model"]["kind"]),
            egress_allowlist=tuple(data.get("egress_allowlist") or ()),
            oauth=(
                OAuthDescriptor.from_manifest(data["oauth"])
                if data.get("oauth")
                else None
            ),
            deployment=(
                DeploymentDescriptor.from_manifest(data["deployment"])
                if data.get("deployment")
                else None
            ),
            surfaces=tuple(
                SurfaceDescriptor.from_manifest(s) for s in (data.get("surfaces") or [])
            ),
            constraints=tuple(
                ConstraintDescriptor.from_manifest(c)
                for c in (data.get("constraints") or [])
            ),
        )


# A connector's icon travels self-contained in its manifest as a data: URI, so the
# platform renders the connector's own branding with no core-hosted asset. The cap
# bounds GET /connectors/types, which inlines every connector's icon.
# It is a string-length cap, not decode-and-measure — catalog bloat is the only risk.
_ICON_DATA_URI_PREFIXES = ("data:image/svg+xml", "data:image/png")
_ICON_MAX_LEN = 256_000


def validate_icon(icon: str | None) -> None:
    """Validate a connector manifest's ``icon``; raise ``ValueError`` if unusable.

    ``None`` is allowed (the UI falls back to a generic icon). A present icon must be
    a self-contained ``data:`` SVG or PNG URI under :data:`_ICON_MAX_LEN`, so a
    subscribed connector ships its own branding without a core-hosted asset and
    cannot bloat the inlined catalog.
    """
    if icon is None:
        return
    if not icon.startswith(_ICON_DATA_URI_PREFIXES):
        raise ValueError(
            "connector icon must be a self-contained data:image/svg+xml "
            "or data:image/png URI"
        )
    if len(icon) > _ICON_MAX_LEN:
        raise ValueError(
            f"connector icon exceeds {_ICON_MAX_LEN} bytes; ship a smaller logo"
        )


class ConnectorProvider(ABC):
    """Provider-specific behavior for one connector type.

    One instance represents one connector. Call sites resolve a provider from the
    registry and delegate connector_type-specific work here instead of branching on
    string equality.
    """

    @property
    @abstractmethod
    def connector_type(self) -> str:
        """Stable identifier stored on ``Connector.connector_type``."""

    def config_schema(self) -> ConfigSchema:
        """Return this connector's declarative admin-config contract.

        The framework derives both validation (the default :meth:`validate_config`)
        and the admin-form JSON Schema (:meth:`ConfigSchema.to_json_schema`) from
        this single declaration. Default: no fields (permissive).
        """
        return ConfigSchema()

    def validate_config(self, config: dict[str, Any]) -> None:
        """Validate admin-supplied connector config; raise on invalid.

        Default: validate against :meth:`config_schema`, so a connector that
        declares a schema gets validation for free. A provider whose validation the
        schema cannot express may still override, raising
        ``InvalidConfigException`` on failure.
        """
        self.config_schema().validate(config)

    def missing_scopes(self, surface: str, scopes: set[str]) -> list[str]:
        """Return the OAuth scopes the user lacks for *surface*, or [] if satisfied.

        ``scopes`` is the user's current normalized scope set. The default has no
        per-surface scope requirements — service-token / remote connectors grant
        access by configuration, not per-surface OAuth scopes. Per-user OAuth
        providers override with their own scope map.
        """
        _ = (surface, scopes)
        return []

    def spec(self) -> ConnectorSpec:
        """Return this connector's self-description (identity defaults to connector_type)."""
        return ConnectorSpec(
            connector_type=self.connector_type,
            provider_id=self.connector_type,
            provider_label=self.connector_type,
        )

    def to_manifest(self) -> dict[str, Any]:
        """Return the connector's full self-description for registration / the UI.

        Combines the identity/auth/egress spec with the admin-config declaration so
        one manifest carries everything the platform needs to register, validate, and
        render the connector. ``config_schema`` is the validation-faithful JSON
        Schema; ``config_fields`` is the ordered rich form spec the admin UI renders
        the configuration form from (labels, groups, options, defaults, validation),
        so the platform ships no connector-specific form.
        """
        schema = self.config_schema()
        manifest = self.spec().to_manifest()
        manifest["config_schema"] = schema.to_json_schema()
        manifest["config_fields"] = schema.to_form_spec()
        return manifest
