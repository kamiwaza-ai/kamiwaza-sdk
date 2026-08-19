# kamiwaza_sdk/schemas/connector_surfaces.py

"""Pydantic models for the workroom-scoped connector *surface* API.

Connector *instance* administration (register / update / delete a cluster-wide
connector) lives in :mod:`kamiwaza_sdk.schemas.connectors`. This module covers
the runtime read surface an ordinary member exercises inside a workroom:

* the per-workroom surface catalog (which connectors the caller may use, and
  which of their surfaces are ready),
* per-user connection verification,
* browse / search over one connector surface,
* content fetch for a single node returned by browse or search.

Every model allows extra fields: the platform normalizes provider-specific
payloads into these shapes and adds fields over time, so a strict model would
break existing clients on a platform upgrade. Response models also default
every non-identifying field, so a partially-populated payload degrades to a
usable object instead of raising.
"""

from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The platform's page-size ceiling for the browse/search endpoints.
MAX_SURFACE_PAGE_SIZE = 200
DEFAULT_SURFACE_PAGE_SIZE = 50
# The platform's ceiling on a surface search query.
MAX_SEARCH_QUERY_LENGTH = 200
# Content types the platform serves as text; anything else is opaque bytes.
TEXT_CONTENT_TYPE_PREFIXES = ("text/", "application/json", "application/xml")
# The workroom registration state that makes a surface usable.
READY_WORKROOM_STATE = "ready"
# The per-user connection states that make a connection unusable no matter what
# the individual capability checks report.
UNUSABLE_CONNECTION_STATUSES = frozenset(
    {"disconnected", "failed", "needs_connection", "needs_reauth"}
)

# A provider locator value: the platform round-trips only scalars here.
LocatorValue = Union[str, int, float, bool]


class ConnectorSurfaceRef(BaseModel):
    """The workroom + connector instance every surface operation targets.

    The pair is inseparable — a connector id means nothing without the workroom
    whose registration authorizes it — so it travels as one value rather than as
    two parallel arguments that could drift apart at a call site.
    """

    model_config = ConfigDict(frozen=True)

    workroom_id: str = Field(..., min_length=1)
    connector_id: str = Field(..., min_length=1)

    @field_validator("workroom_id", "connector_id", mode="before")
    @classmethod
    def _coerce_identifier(cls, value: Any) -> Any:
        """Accept a ``UUID`` as readily as the string form the API uses."""
        return str(value) if isinstance(value, UUID) else value


class ConnectorConstraint(BaseModel):
    """A machine-readable provider or workflow constraint on a surface or node."""

    model_config = ConfigDict(extra="allow")

    code: str = ""
    message: str = ""
    actions: List[str] = Field(default_factory=list)
    source_types: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConnectorContentHandle(BaseModel):
    """Opaque handle describing how to fetch one node's content.

    ``query`` carries provider locator values (e.g. a drive id) that must be
    copied verbatim into the content request; the SDK never interprets them.
    """

    model_config = ConfigDict(extra="allow")

    method: str = "GET"
    path: str = ""
    query: Dict[str, str] = Field(default_factory=dict)
    available: bool = True
    mime_type: Optional[str] = None
    filename: Optional[str] = None


class ConnectorSurfaceCapability(BaseModel):
    """Capability and readiness metadata for one logical surface."""

    model_config = ConfigDict(extra="allow")

    surface: str = ""
    display_label: Optional[str] = None
    browse_supported: bool = True
    list_supported: bool = True
    search_supported: bool = False
    freshness_supported: bool = False
    supported_node_types: List[str] = Field(default_factory=list)
    supported_granularity: List[str] = Field(default_factory=list)
    workroom_state: str = "unknown"
    constraints: List[ConnectorConstraint] = Field(default_factory=list)

    @property
    def label(self) -> str:
        """The human-facing label, falling back to the surface name."""
        return self.display_label or self.surface

    @property
    def ready(self) -> bool:
        """Whether this surface is connected, registered, and healthy.

        A named surface whose workroom registration is anything other than
        ``ready`` must not be browsed or searched — the platform rejects it,
        and treating "unknown" as usable is exactly the fail-open mistake this
        property exists to prevent.
        """
        return bool(self.surface) and self.workroom_state.lower() == READY_WORKROOM_STATE


class ConnectorCapabilities(BaseModel):
    """Top-level capability declaration for one connector instance."""

    model_config = ConfigDict(extra="allow")

    browse_supported: bool = True
    list_supported: bool = True
    search_supported: bool = False
    freshness_supported: bool = False
    supported_source_types: List[str] = Field(default_factory=list)
    supported_granularity: Dict[str, List[str]] = Field(default_factory=dict)
    surfaces: List[ConnectorSurfaceCapability] = Field(default_factory=list)
    constraints: List[ConnectorConstraint] = Field(default_factory=list)


class ConnectorRoutingMetadata(BaseModel):
    """Routing hints the platform publishes for generic connector consumers."""

    model_config = ConfigDict(extra="allow")

    surface_base_path: str = ""
    supported_surfaces: List[str] = Field(default_factory=list)


class ConnectorReauthMetadata(BaseModel):
    """Deep-link targets for reconnecting or completing workroom setup."""

    model_config = ConfigDict(extra="allow")

    connector_settings_url: Optional[str] = None
    credential_registration_url: Optional[str] = None
    message: Optional[str] = None


class ConnectorCatalogItem(BaseModel):
    """One connector instance as the acting user/workroom sees it.

    Richer than :class:`~kamiwaza_sdk.schemas.connectors.AvailableConnector`:
    it carries the configured instance id, the caller's own connection state,
    the workroom registration state, and the manifest-declared surfaces.
    """

    model_config = ConfigDict(extra="allow")

    id: UUID
    provider: str = ""
    provider_label: str = ""
    icon: Optional[str] = None
    connector_type: str = ""
    name: str = ""
    display_label: str = ""
    auth_state: str = "unknown"
    workroom_state: str = "unknown"
    connected_email: Optional[str] = None
    capabilities: ConnectorCapabilities = Field(default_factory=ConnectorCapabilities)
    routing_metadata: Optional[ConnectorRoutingMetadata] = None
    reauth: Optional[ConnectorReauthMetadata] = None

    @property
    def connected(self) -> bool:
        """Whether the acting user's own account is connected to this provider."""
        return self.auth_state.lower() == "connected"

    @property
    def label(self) -> str:
        """The human-facing provider label, degrading to the connector type."""
        return self.provider_label or self.provider or self.connector_type or "Connector"

    def ready_surfaces(self) -> List[ConnectorSurfaceCapability]:
        """The surfaces that are ready to browse for this workroom."""
        return [surface for surface in self.capabilities.surfaces if surface.ready]

    def searchable_surfaces(self) -> List[ConnectorSurfaceCapability]:
        """The ready surfaces that also support full-text search."""
        return [surface for surface in self.ready_surfaces() if surface.search_supported]


class ConnectorSourceRef(BaseModel):
    """Provider identifiers round-tripped through the generic node contract."""

    model_config = ConfigDict(extra="allow")

    provider: str = ""
    connector_id: Optional[UUID] = None
    surface: str = ""
    external_id: str = ""
    container_id: Optional[str] = None
    parent_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConnectorNode(BaseModel):
    """One normalized browse/search result."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    label: str = ""
    node_type: str = ""
    provider: str = ""
    connector_id: Optional[UUID] = None
    surface: str = ""
    is_container: bool = False
    container_id: Optional[str] = None
    parent_id: Optional[str] = None
    web_url: Optional[str] = None
    modified_at: Optional[str] = None
    size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    owner: Optional[str] = None
    summary: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_ref: Optional[ConnectorSourceRef] = None
    content_handle: Optional[ConnectorContentHandle] = None
    freshness_token: Optional[str] = None
    constraints: List[ConnectorConstraint] = Field(default_factory=list)

    @property
    def fetchable(self) -> bool:
        """Whether the platform published a usable content handle for this node."""
        return self.content_handle is not None and self.content_handle.available

    def content_locator(self) -> Dict[str, str]:
        """The provider locator map to copy into a content request, if any."""
        return dict(self.content_handle.query) if self.content_handle else {}


class ConnectorNodePage(BaseModel):
    """One page of browse or search results.

    ``next_page_token`` is opaque: pass it back unchanged as
    :attr:`ConnectorBrowseRequest.page_token` to fetch the following page. It is
    ``None`` on the last page.
    """

    model_config = ConfigDict(extra="allow")

    connector_id: Optional[UUID] = None
    surface: str = ""
    items: List[ConnectorNode] = Field(default_factory=list)
    next_page_token: Optional[str] = None
    constraints: List[ConnectorConstraint] = Field(default_factory=list)

    @property
    def has_more(self) -> bool:
        """Whether another page is available."""
        return bool(self.next_page_token)


class ConnectorCapabilityCheck(BaseModel):
    """One capability probe inside a connection verification result."""

    model_config = ConfigDict(extra="allow")

    capability: str = ""
    label: Optional[str] = None
    status: str = ""
    message: Optional[str] = None
    sample_data: Optional[Dict[str, Any]] = None
    http_status: Optional[int] = None
    failure_kind: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.status.lower() == "success"


class ConnectorVerification(BaseModel):
    """The platform's verdict on the acting user's connection to a connector.

    This is a readiness probe, not a content sync: it reports whether the
    caller's stored credential still reaches the provider right now. Producing
    it is a *write* — the platform persists the classified health it observes —
    so see
    :meth:`~kamiwaza_sdk.services.connector_surfaces.ConnectorSurfaceMixin.verify_connection`
    before calling it on a schedule.
    """

    model_config = ConfigDict(extra="allow")

    status: str = ""
    checks: List[ConnectorCapabilityCheck] = Field(default_factory=list)
    message: Optional[str] = None
    provider_label: Optional[str] = None
    connection_status: str = "connected"
    failing_capabilities: List[str] = Field(default_factory=list)

    @property
    def connection_usable(self) -> bool:
        """Whether the stored connection is usable at all.

        ``False`` for the connection states that require the user to reconnect
        or re-consent, regardless of what the capability checks report.

        This is a deny-list, so a connection state the platform adds later reads
        as usable here. That is deliberate on two counts: it reproduces exactly
        the behavior of the agent runtime this contract replaces, which the
        migration must preserve, and it is not load-bearing on its own —
        :attr:`available` additionally requires :attr:`checks_passed`, which
        *is* an allow-list. An unrecognized connection status therefore cannot
        by itself make a connector available.
        """
        return self.connection_status.lower() not in UNUSABLE_CONNECTION_STATUSES

    @property
    def checks_passed(self) -> bool:
        """Whether the capability checks amount to a usable result.

        ``success`` passes outright; ``partial`` passes only when at least one
        individual check succeeded; every other status fails.
        """
        status = self.status.lower()
        if status == "success":
            return True
        if status != "partial":
            return False
        return any(check.succeeded for check in self.checks)

    @property
    def available(self) -> bool:
        """The fail-closed verdict: usable connection *and* a passing check.

        Anything ambiguous — an unrecognized status, an empty ``partial``, an
        unusable connection state — resolves to ``False``, so a caller that
        gates on this never grants access it could not confirm.
        """
        return self.connection_usable and self.checks_passed


class ConnectorSurfaceContent(BaseModel):
    """The bytes of one connector node, plus what is needed to interpret them."""

    model_config = ConfigDict(extra="allow")

    node_id: str
    surface: str
    content_type: str = ""
    content: bytes = b""
    filename: Optional[str] = None
    status_code: int = 200

    @property
    def size_bytes(self) -> int:
        return len(self.content)

    @property
    def is_text(self) -> bool:
        """Whether the payload should be read as text.

        A missing content type counts as text, matching how the platform serves
        provider payloads that declare no type.
        """
        return not self.content_type or self.content_type.startswith(
            TEXT_CONTENT_TYPE_PREFIXES
        )

    @property
    def is_partial(self) -> bool:
        """Whether the platform answered a range request with partial content."""
        return self.status_code == 206

    def text(self, *, limit: Optional[int] = None) -> str:
        """Decode the payload as UTF-8, replacing undecodable bytes.

        Args:
            limit: Optional maximum number of characters to return. Callers with
                a context budget should pass one rather than truncating after
                the fact.
        """
        decoded = self.content.decode("utf-8", errors="replace")
        return decoded if limit is None else decoded[:limit]


def _clamp_page_size(value: Optional[int]) -> int:
    """Clamp a page size into the platform's accepted range."""
    if value is None:
        return DEFAULT_SURFACE_PAGE_SIZE
    return max(1, min(int(value), MAX_SURFACE_PAGE_SIZE))


class ConnectorBrowseRequest(BaseModel):
    """Request to list the items of one connector surface.

    ``page_size`` is clamped into the platform's accepted 1..200 range rather
    than rejected, so a caller passing a larger ceiling gets the largest page
    the platform allows instead of a 422.
    """

    model_config = ConfigDict(extra="allow")

    surface: str = Field(..., min_length=1, max_length=64)
    container_id: Optional[str] = None
    page_size: int = Field(default=DEFAULT_SURFACE_PAGE_SIZE)
    page_token: Optional[str] = None
    view: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    def to_params(self) -> Dict[str, Any]:
        """The query parameters for the browse endpoint, omitting unset fields."""
        params: Dict[str, Any] = {
            "surface": self.surface,
            "page_size": _clamp_page_size(self.page_size),
        }
        optional = {
            "container_id": self.container_id,
            "page_token": self.page_token,
            "view": self.view,
            "start_date": self.start_date,
            "end_date": self.end_date,
        }
        params.update({key: value for key, value in optional.items() if value})
        return params


class ConnectorSearchRequest(BaseModel):
    """Request to full-text search one connector surface.

    Only surfaces whose capability declares ``search_supported`` accept this;
    the platform answers 501 for the rest.
    """

    model_config = ConfigDict(extra="allow")

    surface: str = Field(..., min_length=1, max_length=64)
    query: str = Field(..., min_length=1, max_length=MAX_SEARCH_QUERY_LENGTH)
    container_id: Optional[str] = None
    page_size: int = Field(default=DEFAULT_SURFACE_PAGE_SIZE)
    page_token: Optional[str] = None

    def to_params(self) -> Dict[str, Any]:
        """The query parameters for the search endpoint, omitting unset fields.

        ``query`` is sent as the endpoint's ``q`` parameter.
        """
        params: Dict[str, Any] = {
            "surface": self.surface,
            "q": self.query,
            "page_size": _clamp_page_size(self.page_size),
        }
        optional = {
            "container_id": self.container_id,
            "page_token": self.page_token,
        }
        params.update({key: value for key, value in optional.items() if value})
        return params


class ConnectorContentRequest(BaseModel):
    """Request for one node's content.

    ``drive_id`` and ``mime_type`` are the locators the platform declares today.
    ``locator`` carries any further provider locator values copied from the
    node's :attr:`ConnectorNode.content_handle` query.

    Two things about ``locator`` are worth knowing before relying on it:

    * A locator entry named ``drive_id`` or ``mime_type`` **fills in** the
      matching declared field when that field is unset. The platform copies a
      connector's own content query verbatim into ``content_handle.query``, so a
      connector may ship its export ``mime_type`` there and nowhere else;
      dropping it would silently fetch the wrong representation.
    * Any *other* locator key is still sent, but the platform's content route
      declares only ``surface``, ``drive_id`` and ``mime_type`` — so an
      undeclared key is discarded server-side today. Passing one is harmless and
      costs nothing if the route later declares it, but it is not a substitute
      for a platform change.

    An explicitly-set declared field always wins over a locator entry of the
    same name, and ``surface`` is never accepted as a locator key.
    """

    model_config = ConfigDict(extra="allow")

    surface: str = Field(..., min_length=1, max_length=64)
    drive_id: Optional[str] = None
    mime_type: Optional[str] = None
    filename: Optional[str] = None
    locator: Dict[str, LocatorValue] = Field(default_factory=dict)

    def to_params(self) -> Dict[str, Any]:
        """The query parameters for the content endpoint, omitting unset fields.

        A ``drive_id`` / ``mime_type`` carried only in ``locator`` is promoted
        into the declared parameter rather than dropped; an explicitly-set field
        takes precedence over the locator entry of the same name.
        """
        declared = {"surface", "drive_id", "mime_type"}
        params: Dict[str, Any] = {
            key: value
            for key, value in self.locator.items()
            if key and key not in declared
        }
        params["surface"] = self.surface
        drive_id = self.drive_id or self.locator.get("drive_id")
        mime_type = self.mime_type or self.locator.get("mime_type")
        if drive_id:
            params["drive_id"] = str(drive_id)
        if mime_type:
            params["mime_type"] = str(mime_type)
        return params

    @classmethod
    def from_node(
        cls, node: ConnectorNode, *, surface: Optional[str] = None
    ) -> "ConnectorContentRequest":
        """Build a content request straight from a browse/search result.

        The node's own surface and content-handle locator are used, so callers
        never hand-assemble provider locators.
        """
        handle = node.content_handle
        locator = dict(handle.query) if handle else {}
        return cls(
            surface=surface or node.surface,
            drive_id=str(locator.pop("drive_id")) if "drive_id" in locator else None,
            mime_type=(handle.mime_type if handle else None) or node.mime_type,
            filename=(handle.filename if handle else None) or node.label or None,
            locator={key: value for key, value in locator.items() if key != "surface"},
        )
