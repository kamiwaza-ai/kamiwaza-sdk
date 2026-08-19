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

**Failures are not flattened.** These endpoints distinguish "not permitted"
(403), "surface not ready in this workroom" (409), "operation unsupported for
this surface" (501), "upstream provider error" (502) and "verification
unavailable" (503). The methods below let :class:`~kamiwaza_sdk.exceptions.APIError`
propagate with its ``status_code`` intact rather than collapsing everything to a
single not-found, because callers must be able to tell a permanent denial from a
transient outage — a distinction a caller cannot recover once it is lost.
"""

from typing import Any, Dict, Iterator, List, Union
from urllib.parse import quote
from uuid import UUID

from ..schemas.connector_surfaces import (
    ConnectorBrowseRequest,
    ConnectorCatalogItem,
    ConnectorContentRequest,
    ConnectorNode,
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


class ConnectorSurfaceMixin:
    """Connector surface discovery and read operations for one workroom."""

    # Supplied by BaseService on the service this mixin is composed into.
    client: Any

    def _surfaces_root(self, workroom_id: Union[str, UUID]) -> str:
        """The per-workroom connector-surfaces API root."""
        return f"/connectors/surfaces/workrooms/{_segment(workroom_id)}"

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
            timeout: Transport timeout in seconds.

        Returns:
            The catalog entries, empty when the caller has no usable connector.

        Raises:
            APIError: With ``status_code`` 403 when the caller may not view the
                workroom, or the platform's own status for other failures.
        """
        response = self.client.get(
            f"{self._surfaces_root(workroom_id)}/catalog", timeout=timeout
        )
        items = response.get("items", []) if isinstance(response, dict) else response
        return [ConnectorCatalogItem.model_validate(item) for item in items or []]

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
            APIError: With ``status_code`` 400 (a connector id this workroom
                does not resolve), 403 (not permitted), 409 (surface not ready
                in this workroom), 501 (surface does not support browse), or
                502 (upstream provider error).
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
            APIError: With ``status_code`` 400 (a connector id this workroom
                does not resolve), 403 (not permitted), 409 (surface not ready),
                501 (surface does not support search), or 502 (upstream
                provider error).
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
                (surface not ready), or 502 (upstream provider error).
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
            filename=request.filename,
            status_code=response.status_code,
        )

    def iter_surface_nodes(
        self,
        ref: ConnectorSurfaceRef,
        request: ConnectorBrowseRequest,
        *,
        max_pages: int = 10,
        timeout: float = DEFAULT_SURFACE_TIMEOUT_SECONDS,
    ) -> Iterator[ConnectorNode]:
        """Yield browse results across pages, up to ``max_pages``.

        Bounded on purpose: a connector surface can be arbitrarily large, so an
        unbounded walk is a way to hang a caller on a provider's paging. Raise
        ``max_pages`` deliberately when a caller really wants more.

        Args:
            ref: The workroom and connector instance to browse.
            request: The first page's request; its ``page_token`` is advanced.
            max_pages: Maximum number of pages to request.
            timeout: Per-request transport timeout in seconds.

        Yields:
            Each :class:`~kamiwaza_sdk.schemas.connector_surfaces.ConnectorNode`
            in page order.

        Raises:
            ValueError: If ``max_pages`` is less than 1.
        """
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        page_request = request
        for _ in range(max_pages):
            page = self.browse_surface(ref, page_request, timeout=timeout)
            yield from page.items
            if not page.next_page_token:
                return
            page_request = page_request.model_copy(
                update={"page_token": page.next_page_token}
            )

    def list_connector_surfaces(
        self,
        workroom_id: Union[str, UUID],
        *,
        connected_only: bool = False,
        timeout: float = DEFAULT_CATALOG_TIMEOUT_SECONDS,
    ) -> List[ConnectorCatalogItem]:
        """The surface catalog, optionally narrowed to connected instances.

        Args:
            workroom_id: The workroom whose catalog to read.
            connected_only: When true, drop instances the caller has not
                connected their own account to.
            timeout: Transport timeout in seconds.

        Returns:
            The matching catalog entries.
        """
        items = self.list_surface_catalog(workroom_id, timeout=timeout)
        if not connected_only:
            return items
        return [item for item in items if item.connected]
