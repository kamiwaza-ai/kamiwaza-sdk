# kamiwaza_sdk/services/connector_surfaces.py

"""Workroom-scoped connector *surface* operations.

Mixed into :class:`~kamiwaza_sdk.services.connectors.ConnectorService`, this adds
the runtime read path an ordinary member exercises inside a workroom — surface
catalog, connection verification, browse, search, and content fetch — alongside
the instance administration the service already wraps.

**Identity.** Every call travels on the client's own credential; the platform
re-evaluates the caller's provider ACLs on each request and brokers the provider
token itself. Nothing here accepts, constructs, or stores a provider credential,
so an agent runtime forwarding a member envelope keeps per-user authorization
without ever holding a provider secret.

**Failures are not flattened.** These endpoints distinguish "bad request, or a
surface that is unknown or not ready in this workroom" (400), "not permitted"
(403), "this member has no connection to the connector" (409), "no such
operation, *or* the connector is not deployed" (501), "upstream provider error"
(502) and "connector not deployed, verification impossible" (503). The methods
below let :class:`~kamiwaza_sdk.exceptions.APIError` propagate with its
``status_code`` intact rather than collapsing everything to a single not-found,
because callers must be able to tell a permanent denial from a transient outage
— a distinction a caller cannot recover once it is lost.

**501 is the one status that is genuinely ambiguous**, and the platform gives
callers no way to disambiguate it: the same code covers "this surface declares
no such operation" (permanent) and "the connector is not reachable right now"
(transient, during a rollout). Treat a 501 as retryable-with-backoff at least
once before recording a surface as unsupported.

**The one exception is 401, and it is not ours to change.**
:class:`~kamiwaza_sdk.client.KamiwazaClient` intercepts every 401 before service
code runs: it refreshes the credential and retries once, then raises
:class:`~kamiwaza_sdk.exceptions.AuthenticationError`. That type descends from
:class:`~kamiwaza_sdk.exceptions.KamiwazaError`, **not** ``APIError``, and
carries no ``status_code``. So a caller matching ``except APIError as exc: if
exc.status_code == 401`` will never fire on these methods — catch
``AuthenticationError`` separately. Note also that because the client retries
before raising, an expired credential costs a duplicate content download.
"""

from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote, unquote
from uuid import UUID

from ..schemas.connector_surfaces import (
    ConnectorBrowseRequest,
    ConnectorCatalogItem,
    ConnectorContentRequest,
    ConnectorNodePage,
    ConnectorSearchRequest,
    ConnectorSurfaceContent,
    ConnectorSurfaceRef,
    ConnectorVerification,
)

# Bounded transport budgets. The catalog is a local metadata read; browse,
# search, and content all round-trip to a third-party provider, so they get the
# longer budget. Every method takes a ``timeout`` override for callers whose own
# request budget is tighter.
DEFAULT_CATALOG_TIMEOUT_SECONDS = 15.0
DEFAULT_SURFACE_TIMEOUT_SECONDS = 30.0
DEFAULT_VERIFY_TIMEOUT_SECONDS = 30.0


def _segment(value: Union[str, UUID]) -> str:
    """Percent-encode one path segment, escaping separators."""
    return quote(str(value), safe="")


# Characters that would let a provider-supplied filename escape the directory a
# caller joins it onto.
_UNSAFE_FILENAME_CHARS = ("/", "\\", "\x00")


def _safe_filename(candidate: str) -> Optional[str]:
    """A filename safe to join onto a directory, or ``None`` if it isn't.

    ``Content-Disposition`` is the third-party provider's text — the platform
    filters which headers pass through, not what they contain — so this value is
    untrusted. Callers routinely do ``open(os.path.join(dest, name), "wb")``, so
    anything carrying a path separator or a parent reference is rejected rather
    than sanitized into something that merely looks safe.
    """
    name = candidate.strip().strip('"').strip()
    if not name or name in {".", ".."}:
        return None
    if any(char in name for char in _UNSAFE_FILENAME_CHARS):
        return None
    return name


def _disposition_params(disposition: str):
    """Yield ``(lowercased key, raw value)`` for each parameter in the header."""
    for part in disposition.split(";"):
        key, separator, raw = part.strip().partition("=")
        if separator:
            yield key.strip().lower(), raw


def _extended_filename(raw: str) -> Optional[str]:
    """Decode an RFC 5987 ``charset'language'percent-encoded-value`` parameter."""
    return _safe_filename(unquote(raw.strip().strip('"').rpartition("'")[2]))


def _served_filename(response: Any) -> Optional[str]:
    """The filename the platform advertised, if it sent a usable one.

    Prefers RFC 5987's ``filename*`` (the form providers use for any non-ASCII
    name) over the plain ``filename``. Only ``filename*`` is percent-decoded —
    decoding a plain ``filename`` both corrupts a literal ``%20`` in a real name
    and can manufacture a path separator that was not in the header.
    """
    plain: Optional[str] = None
    for key, raw in _disposition_params(response.headers.get("content-disposition", "")):
        if key == "filename*":
            extended = _extended_filename(raw)
            if extended:
                return extended
        elif key == "filename" and plain is None:
            plain = _safe_filename(raw)
    return plain


class ConnectorSurfaceMixin:
    """Connector surface discovery and read operations for one workroom."""

    # Supplied by BaseService on the service this mixin is composed into.
    client: Any

    def _surfaces_root(self, workroom_id: Union[str, UUID]) -> str:
        """The per-workroom connector-surfaces API root.

        Raises:
            ValueError: If ``workroom_id`` is empty — an empty segment would
                build ``/workrooms//catalog``, which reads as a different route
                rather than as the mistake it is.
        """
        segment = _segment(str(workroom_id).strip())
        if not segment:
            raise ValueError("workroom_id is required for connector surface calls")
        return f"/connectors/surfaces/workrooms/{segment}"

    def _surface_base(self, ref: ConnectorSurfaceRef) -> str:
        """The surface-operation base path for one configured connector instance."""
        root = self._surfaces_root(ref.workroom_id)
        return f"{root}/{_segment(ref.connector_id)}"

    def _nodes_page(
        self,
        ref: ConnectorSurfaceRef,
        action: str,
        params: Dict[str, Any],
        timeout: float,
    ) -> ConnectorNodePage:
        """GET one page of nodes from the ``browse`` or ``search`` endpoint."""
        response = self.client.get(
            f"{self._surface_base(ref)}/{action}", params=params, timeout=timeout
        )
        return ConnectorNodePage.model_validate(response or {})

    def list_surface_catalog(
        self,
        workroom_id: Union[str, UUID],
        *,
        connected_only: bool = False,
        timeout: float = DEFAULT_CATALOG_TIMEOUT_SECONDS,
    ) -> List[ConnectorCatalogItem]:
        """List the connector surfaces available to the caller in a workroom.

        Richer than :meth:`~kamiwaza_sdk.services.connectors.ConnectorService.list_available`:
        each entry carries the configured instance id, the caller's own
        connection state, the workroom registration state, and the
        manifest-declared surfaces with their readiness.

        Only connectors the caller is authorized to use in ``workroom_id`` are
        returned — this is the authoritative discovery call, so a connector
        absent here must not be reached by any other means.

        Args:
            workroom_id: The workroom whose catalog to read.
            connected_only: When true, drop instances whose own account the
                caller has not connected. The platform still returns them, since
                knowing a connector exists is what lets a UI offer to connect it.
            timeout: Transport timeout in seconds.

        Returns:
            The catalog entries, empty when the caller has no usable connector.

        Raises:
            ValueError: If ``workroom_id`` is empty.
            APIError: With ``status_code`` 403 when the caller may not view the
                workroom, or the platform's own status for other failures.
        """
        response = self.client.get(
            f"{self._surfaces_root(workroom_id)}/catalog", timeout=timeout
        )
        items = response.get("items", []) if isinstance(response, dict) else response
        entries = [ConnectorCatalogItem.model_validate(item) for item in items or []]
        if not connected_only:
            return entries
        return [entry for entry in entries if entry.connected]

    def verify_connection(
        self,
        connector_id: Union[str, UUID],
        *,
        timeout: float = DEFAULT_VERIFY_TIMEOUT_SECONDS,
    ) -> ConnectorVerification:
        """Verify the caller's own connection to a connector, right now.

        The platform probes the provider under the caller's stored credential
        and returns per-capability results. Gate on
        :attr:`~kamiwaza_sdk.schemas.connector_surfaces.ConnectorVerification.available`
        for the fail-closed verdict rather than reading ``status`` directly.

        **This writes, despite the name.** Unlike every other method in this
        mixin, verification is not a pure read: the platform classifies the
        probe and *persists* the resulting connection health, so a verify can
        move the member's stored connection into a degraded or reauth-required
        state. It also issues live calls to the third-party provider. Call it
        deliberately — on connect, on an explicit user action, or when a surface
        call already failed — and do not poll it on a timer or fan it out across
        a catalog.

        Args:
            connector_id: The configured connector instance to verify.
            timeout: Transport timeout in seconds.

        Returns:
            The verification result.

        Raises:
            APIError: With ``status_code`` 404 when the connector does not
                exist, or 503 when it is not deployed yet and cannot be probed.
        """
        response = self.client.post(
            f"/connectors/{_segment(connector_id)}/verify", timeout=timeout
        )
        return ConnectorVerification.model_validate(response or {})

    def browse_surface(
        self,
        ref: ConnectorSurfaceRef,
        request: ConnectorBrowseRequest,
        *,
        timeout: float = DEFAULT_SURFACE_TIMEOUT_SECONDS,
    ) -> ConnectorNodePage:
        """List the items of one connector surface.

        Pass ``request.container_id`` to descend into a container, and the prior
        page's ``next_page_token`` as ``request.page_token`` to continue paging.

        Args:
            ref: The workroom and connector instance to browse.
            request: Surface, optional container, and paging.
            timeout: Transport timeout in seconds.

        Returns:
            One page of nodes.

        Raises:
            APIError: With ``status_code`` 400 (a connector id this workroom does
                not resolve, an unknown surface, or a surface whose workroom
                registration is not ``ready``), 403 (not permitted), 409 (the
                member has no connection to this connector — send them to the
                catalog entry's ``reauth`` deep links), 501 (either the surface
                genuinely has no browse op, or the connector is not deployed
                right now — see the note below), or 502 (upstream provider
                error).

        Note:
            A surface that is merely *not ready in this workroom* answers 400,
            not 409: the platform raises it as a request error alongside an
            unknown surface name. Do not treat it as a connection problem.
        """
        return self._nodes_page(ref, "browse", request.to_params(), timeout)

    def search_surface(
        self,
        ref: ConnectorSurfaceRef,
        request: ConnectorSearchRequest,
        *,
        timeout: float = DEFAULT_SURFACE_TIMEOUT_SECONDS,
    ) -> ConnectorNodePage:
        """Full-text search one connector surface.

        Only surfaces whose capability declares ``search_supported`` accept
        this; check
        :meth:`~kamiwaza_sdk.schemas.connector_surfaces.ConnectorCatalogItem.searchable_surfaces`
        first rather than discovering it as a 501.

        Args:
            ref: The workroom and connector instance to search.
            request: Surface, query, optional container, and paging.
            timeout: Transport timeout in seconds.

        Returns:
            One page of matching nodes.

        Raises:
            APIError: With ``status_code`` 400 (a connector id this workroom does
                not resolve, an unknown surface, or a surface not ``ready`` in
                this workroom), 403 (not permitted), 409 (the member has no
                connection — see the catalog entry's ``reauth`` deep links), 501
                (no search op for this surface, or the connector is not deployed
                right now), or 502 (upstream provider error).
        """
        return self._nodes_page(ref, "search", request.to_params(), timeout)

    def fetch_surface_content(
        self,
        ref: ConnectorSurfaceRef,
        node_id: str,
        request: ConnectorContentRequest,
        *,
        timeout: float = DEFAULT_SURFACE_TIMEOUT_SECONDS,
    ) -> ConnectorSurfaceContent:
        """Fetch one node's content by its opaque node id.

        ``node_id`` comes from a browse or search result and is opaque — it is
        sent percent-encoded and is never parsed. Build ``request`` with
        :meth:`~kamiwaza_sdk.schemas.connector_surfaces.ConnectorContentRequest.from_node`
        so the node's provider locator travels with it.

        The result carries raw bytes plus the platform's content type; use
        :attr:`~kamiwaza_sdk.schemas.connector_surfaces.ConnectorSurfaceContent.is_text`
        to decide whether to decode. Binary payloads are returned as-is rather
        than being decoded or discarded.

        Args:
            ref: The workroom and connector instance owning the node.
            node_id: The node id from a browse/search result.
            request: Surface and provider locator for the node.
            timeout: Transport timeout in seconds.

        Returns:
            The node's content and metadata.

        Raises:
            ValueError: If ``node_id`` is empty.
            APIError: With ``status_code`` 400 (a connector id this workroom
                does not resolve), 403 (not permitted), 404 (node gone), 409
                (connection missing), 413 (content exceeds the platform's cap),
                or 502 (upstream provider error).
        """
        node_id = (node_id or "").strip()
        if not node_id:
            raise ValueError("node_id is required to fetch connector content")
        response = self.client.get(
            f"{self._surface_base(ref)}/content/{_segment(node_id)}",
            params=request.to_params(),
            timeout=timeout,
            expect_json=False,
        )
        return ConnectorSurfaceContent(
            node_id=node_id,
            surface=request.surface,
            content_type=response.headers.get("content-type", ""),
            content=response.content,
            filename=_served_filename(response) or request.filename,
            status_code=response.status_code,
        )
