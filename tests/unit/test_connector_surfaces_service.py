from __future__ import annotations

import uuid

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.connector_surfaces import (
    DEFAULT_SURFACE_PAGE_SIZE,
    MAX_SURFACE_PAGE_SIZE,
    ConnectorBrowseRequest,
    ConnectorContentRequest,
    ConnectorNode,
    ConnectorSearchRequest,
    ConnectorSurfaceRef,
    ConnectorVerification,
)
from kamiwaza_sdk.services.connectors import ConnectorService

pytestmark = pytest.mark.unit

_WORKROOM = "0f1d0f4e-3d3c-4a1e-9a7f-4a2b7c1d5e6f"
_CONNECTOR = "3c2b1a09-8f7e-6d5c-4b3a-2918f7e6d5c4"
_SURFACES_ROOT = f"/connectors/surfaces/workrooms/{_WORKROOM}"
_SURFACE_BASE = f"{_SURFACES_ROOT}/{_CONNECTOR}"
_REF = ConnectorSurfaceRef(workroom_id=_WORKROOM, connector_id=_CONNECTOR)


class _Response:
    """The minimal ``requests``-shaped response the content path reads."""

    def __init__(self, content: bytes, content_type: str, status_code: int = 200):
        self.content = content
        self.headers = {"content-type": content_type}
        self.status_code = status_code


class DummyClient:
    """Records calls and replays canned responses or raises canned errors."""

    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def _reply(self, method, path, kwargs):
        self.calls.append((method, path, kwargs))
        reply = self.responses[(method, path)]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def get(self, path, **kwargs):
        return self._reply("GET", path, kwargs)

    def post(self, path, **kwargs):
        return self._reply("POST", path, kwargs)


def _service(responses) -> tuple[ConnectorService, DummyClient]:
    client = DummyClient(responses)
    return ConnectorService(client), client


def _surface(name="files", *, state="ready", search=True) -> dict:
    return {
        "surface": name,
        "display_label": name.title(),
        "workroom_state": state,
        "browse_supported": True,
        "search_supported": search,
    }


def _catalog_item(*, auth_state="connected", surfaces=None) -> dict:
    return {
        "id": _CONNECTOR,
        "provider": "m365",
        "provider_label": "Microsoft 365",
        "connector_type": "m365",
        "name": "Microsoft 365",
        "display_label": "Microsoft 365",
        "auth_state": auth_state,
        "workroom_state": "ready",
        "capabilities": {"surfaces": surfaces if surfaces is not None else [_surface()]},
        "routing_metadata": {
            "surface_base_path": _SURFACE_BASE,
            "supported_surfaces": ["files"],
        },
    }


def _node(node_id="node-1", *, label="Report.pdf") -> dict:
    return {
        "id": node_id,
        "label": label,
        "node_type": "file",
        "provider": "m365",
        "connector_id": _CONNECTOR,
        "surface": "files",
        "mime_type": "application/pdf",
        "content_handle": {
            "method": "GET",
            "path": f"{_SURFACE_BASE}/content/{node_id}",
            "query": {"drive_id": "drive-9", "site_id": "site-3"},
            "available": True,
            "filename": label,
        },
        "source_ref": {
            "provider": "m365",
            "connector_id": _CONNECTOR,
            "surface": "files",
            "external_id": node_id,
        },
    }


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


def test_list_surface_catalog_returns_typed_entries():
    service, client = _service(
        {("GET", f"{_SURFACES_ROOT}/catalog"): {"items": [_catalog_item()]}}
    )

    items = service.list_surface_catalog(_WORKROOM)

    assert len(items) == 1
    entry = items[0]
    assert entry.id == uuid.UUID(_CONNECTOR)
    assert entry.connected is True
    assert entry.label == "Microsoft 365"
    assert [surface.surface for surface in entry.ready_surfaces()] == ["files"]
    assert [surface.surface for surface in entry.searchable_surfaces()] == ["files"]
    _, _, kwargs = client.calls[0]
    assert kwargs["timeout"] == 15.0


def test_catalog_treats_non_ready_surfaces_as_unusable():
    service, _ = _service(
        {
            ("GET", f"{_SURFACES_ROOT}/catalog"): {
                "items": [
                    _catalog_item(
                        surfaces=[
                            _surface("files", state="pending"),
                            _surface("mail", state="unknown"),
                        ]
                    )
                ]
            }
        }
    )

    entry = service.list_surface_catalog(_WORKROOM)[0]

    assert entry.ready_surfaces() == []
    assert entry.searchable_surfaces() == []


def test_catalog_keeps_unknown_fields_for_forward_compatibility():
    item = _catalog_item()
    item["future_field"] = {"kind": "new"}
    item["capabilities"]["surfaces"][0]["future_surface_flag"] = True
    service, _ = _service({("GET", f"{_SURFACES_ROOT}/catalog"): {"items": [item]}})

    entry = service.list_surface_catalog(_WORKROOM)[0]

    assert entry.future_field == {"kind": "new"}
    assert entry.capabilities.surfaces[0].future_surface_flag is True


def test_catalog_accepts_a_bare_list_payload():
    service, _ = _service({("GET", f"{_SURFACES_ROOT}/catalog"): [_catalog_item()]})

    assert len(service.list_surface_catalog(_WORKROOM)) == 1


def test_catalog_authorization_failure_preserves_status_code():
    service, _ = _service(
        {
            ("GET", f"{_SURFACES_ROOT}/catalog"): APIError(
                "forbidden", status_code=403
            )
        }
    )

    with pytest.raises(APIError) as excinfo:
        service.list_surface_catalog(_WORKROOM)

    assert excinfo.value.status_code == 403


def test_list_connector_surfaces_can_drop_unconnected_instances():
    service, _ = _service(
        {
            ("GET", f"{_SURFACES_ROOT}/catalog"): {
                "items": [
                    _catalog_item(),
                    _catalog_item(auth_state="needs_reauth"),
                ]
            }
        }
    )

    assert len(service.list_connector_surfaces(_WORKROOM)) == 2
    assert len(service.list_connector_surfaces(_WORKROOM, connected_only=True)) == 1


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def test_verify_connection_reports_available_on_success():
    service, client = _service(
        {
            ("POST", f"/connectors/{_CONNECTOR}/verify"): {
                "status": "success",
                "checks": [{"capability": "files", "status": "success"}],
                "connection_status": "connected",
                "message": "All good.",
            }
        }
    )

    result = service.verify_connection(_CONNECTOR)

    assert result.available is True
    assert result.checks[0].succeeded is True
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", f"/connectors/{_CONNECTOR}/verify")
    assert kwargs["timeout"] == 30.0


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"status": "success", "connection_status": "needs_reauth"}, False),
        ({"status": "failure", "connection_status": "connected"}, False),
        ({"status": "partial", "checks": [], "connection_status": "connected"}, False),
        (
            {
                "status": "partial",
                "checks": [{"status": "failure"}, {"status": "success"}],
                "connection_status": "connected",
            },
            True,
        ),
        ({"status": "", "connection_status": "connected"}, False),
        ({"status": "success", "connection_status": "disconnected"}, False),
    ],
)
def test_verification_verdict_is_fail_closed(payload, expected):
    assert ConnectorVerification.model_validate(payload).available is expected


def test_verify_connection_unavailable_preserves_status_code():
    service, _ = _service(
        {
            ("POST", f"/connectors/{_CONNECTOR}/verify"): APIError(
                "not deployed", status_code=503
            )
        }
    )

    with pytest.raises(APIError) as excinfo:
        service.verify_connection(_CONNECTOR)

    assert excinfo.value.status_code == 503


# --------------------------------------------------------------------------
# Browse / search
# --------------------------------------------------------------------------


def test_browse_surface_sends_bounded_params_and_types_the_page():
    service, client = _service(
        {
            ("GET", f"{_SURFACE_BASE}/browse"): {
                "connector_id": _CONNECTOR,
                "surface": "files",
                "items": [_node()],
                "next_page_token": "tok-2",
            }
        }
    )

    page = service.browse_surface(
        _REF,
        ConnectorBrowseRequest(surface="files", container_id="folder-1"),
    )

    assert page.has_more is True
    assert page.items[0].label == "Report.pdf"
    assert page.items[0].fetchable is True
    _, _, kwargs = client.calls[0]
    assert kwargs["params"] == {
        "surface": "files",
        "page_size": DEFAULT_SURFACE_PAGE_SIZE,
        "container_id": "folder-1",
    }
    assert kwargs["timeout"] == 30.0


def test_browse_request_clamps_page_size_into_the_platform_range():
    assert ConnectorBrowseRequest(surface="files", page_size=5_000).to_params()[
        "page_size"
    ] == MAX_SURFACE_PAGE_SIZE
    assert (
        ConnectorBrowseRequest(surface="files", page_size=0).to_params()["page_size"] == 1
    )


def test_search_surface_sends_the_query_as_q():
    service, client = _service(
        {("GET", f"{_SURFACE_BASE}/search"): {"items": [_node()], "surface": "files"}}
    )

    page = service.search_surface(
        _REF,
        ConnectorSearchRequest(surface="files", query="quarterly report", page_size=10),
    )

    assert len(page.items) == 1
    assert page.has_more is False
    _, _, kwargs = client.calls[0]
    assert kwargs["params"] == {
        "surface": "files",
        "q": "quarterly report",
        "page_size": 10,
    }


def test_search_request_rejects_an_empty_query():
    with pytest.raises(ValueError):
        ConnectorSearchRequest(surface="files", query="")


def test_search_on_an_unsupported_surface_preserves_status_code():
    service, _ = _service(
        {("GET", f"{_SURFACE_BASE}/search"): APIError("unsupported", status_code=501)}
    )

    with pytest.raises(APIError) as excinfo:
        service.search_surface(
            _REF, ConnectorSearchRequest(surface="files", query="x")
        )

    assert excinfo.value.status_code == 501


def test_browse_on_a_surface_that_is_not_ready_preserves_status_code():
    service, _ = _service(
        {("GET", f"{_SURFACE_BASE}/browse"): APIError("not ready", status_code=409)}
    )

    with pytest.raises(APIError) as excinfo:
        service.browse_surface(
            _REF, ConnectorBrowseRequest(surface="files")
        )

    assert excinfo.value.status_code == 409


def test_node_page_tolerates_an_empty_payload():
    service, _ = _service({("GET", f"{_SURFACE_BASE}/browse"): None})

    page = service.browse_surface(
        _REF, ConnectorBrowseRequest(surface="files")
    )

    assert page.items == []
    assert page.has_more is False


def test_iter_surface_nodes_follows_pagination_to_the_last_page():
    pages = [
        {"items": [_node("a")], "next_page_token": "tok-2"},
        {"items": [_node("b")], "next_page_token": None},
    ]

    class PagingClient(DummyClient):
        def get(self, path, **kwargs):
            self.calls.append(("GET", path, kwargs))
            return pages[len(self.calls) - 1]

    client = PagingClient({})
    service = ConnectorService(client)

    node_ids = [
        node.id
        for node in service.iter_surface_nodes(
            _REF, ConnectorBrowseRequest(surface="files")
        )
    ]

    assert node_ids == ["a", "b"]
    assert client.calls[1][2]["params"]["page_token"] == "tok-2"


def test_iter_surface_nodes_stops_at_the_page_bound():
    class EndlessClient(DummyClient):
        def get(self, path, **kwargs):
            self.calls.append(("GET", path, kwargs))
            return {"items": [_node()], "next_page_token": "always-more"}

    client = EndlessClient({})
    service = ConnectorService(client)

    nodes = list(
        service.iter_surface_nodes(
            _REF, ConnectorBrowseRequest(surface="files"), max_pages=3
        )
    )

    assert len(nodes) == 3
    assert len(client.calls) == 3


# --------------------------------------------------------------------------
# Content fetch
# --------------------------------------------------------------------------


def test_fetch_surface_content_returns_decodable_text():
    service, client = _service(
        {
            (
                "GET",
                f"{_SURFACE_BASE}/content/node-1",
            ): _Response(b"hello world", "text/plain; charset=utf-8")
        }
    )

    content = service.fetch_surface_content(
        _REF,
        "node-1",
        ConnectorContentRequest(surface="files", drive_id="drive-9"),
    )

    assert content.is_text is True
    assert content.text() == "hello world"
    assert content.text(limit=5) == "hello"
    assert content.size_bytes == 11
    _, _, kwargs = client.calls[0]
    assert kwargs["expect_json"] is False
    assert kwargs["params"] == {"surface": "files", "drive_id": "drive-9"}


def test_fetch_surface_content_leaves_binary_payloads_as_bytes():
    payload = b"%PDF-1.7\x00\x01binary"
    service, _ = _service(
        {("GET", f"{_SURFACE_BASE}/content/node-1"): _Response(payload, "application/pdf")}
    )

    content = service.fetch_surface_content(
        _REF, "node-1", ConnectorContentRequest(surface="files")
    )

    assert content.is_text is False
    assert content.content == payload
    assert content.is_partial is False


def test_missing_content_type_is_treated_as_text():
    service, _ = _service(
        {("GET", f"{_SURFACE_BASE}/content/node-1"): _Response(b"body", "")}
    )

    content = service.fetch_surface_content(
        _REF, "node-1", ConnectorContentRequest(surface="files")
    )

    assert content.is_text is True


def test_fetch_surface_content_reports_partial_responses():
    service, _ = _service(
        {
            ("GET", f"{_SURFACE_BASE}/content/node-1"): _Response(
                b"chunk", "application/octet-stream", status_code=206
            )
        }
    )

    content = service.fetch_surface_content(
        _REF, "node-1", ConnectorContentRequest(surface="files")
    )

    assert content.is_partial is True


def test_fetch_surface_content_percent_encodes_the_node_id():
    encoded = "folder%2Fnode%20one"
    service, client = _service(
        {("GET", f"{_SURFACE_BASE}/content/{encoded}"): _Response(b"x", "text/plain")}
    )

    service.fetch_surface_content(
        _REF, "folder/node one", ConnectorContentRequest(surface="files")
    )

    assert client.calls[0][1] == f"{_SURFACE_BASE}/content/{encoded}"


def test_fetch_surface_content_requires_a_node_id():
    service, _ = _service({})

    with pytest.raises(ValueError):
        service.fetch_surface_content(
            _REF, "   ", ConnectorContentRequest(surface="files")
        )


def test_fetch_surface_content_authorization_failure_preserves_status_code():
    service, _ = _service(
        {("GET", f"{_SURFACE_BASE}/content/node-1"): APIError("denied", status_code=403)}
    )

    with pytest.raises(APIError) as excinfo:
        service.fetch_surface_content(
            _REF, "node-1", ConnectorContentRequest(surface="files")
        )

    assert excinfo.value.status_code == 403


def test_content_request_from_node_carries_the_provider_locator():
    node = ConnectorNode.model_validate(_node())

    request = ConnectorContentRequest.from_node(node)

    assert request.surface == "files"
    assert request.drive_id == "drive-9"
    assert request.filename == "Report.pdf"
    assert request.to_params() == {
        "surface": "files",
        "drive_id": "drive-9",
        "mime_type": "application/pdf",
        "site_id": "site-3",
    }


def test_undeclared_locator_keys_are_sent_though_the_platform_ignores_them():
    """The SDK does not filter unknown locator keys, but they are not a feature.

    The platform's content route declares only surface/drive_id/mime_type, so an
    undeclared key is discarded server-side. This asserts the SDK's behavior, not
    a forward-compatibility guarantee the platform actually honors.
    """
    request = ConnectorContentRequest(surface="files", locator={"site_id": "site-3"})

    assert request.to_params() == {"surface": "files", "site_id": "site-3"}


def test_content_request_locator_cannot_override_declared_parameters():
    request = ConnectorContentRequest(
        surface="files",
        drive_id="real-drive",
        locator={"surface": "spoofed", "drive_id": "spoofed", "site_id": "site-3"},
    )

    assert request.to_params() == {
        "surface": "files",
        "drive_id": "real-drive",
        "site_id": "site-3",
    }


def test_locator_only_mime_type_is_promoted_not_dropped():
    """A connector may ship its export mime_type only inside content_handle.query.

    Core copies a connector's own content query verbatim, so the export type can
    arrive solely as a locator entry. Dropping it silently fetches the wrong
    representation — the regression this guards.
    """
    node = ConnectorNode.model_validate(
        {
            "id": "doc-1",
            "surface": "files",
            "label": "Design",
            "content_handle": {
                "query": {"drive_id": "drive-9", "mime_type": "application/pdf"},
                "available": True,
            },
        }
    )

    params = ConnectorContentRequest.from_node(node).to_params()

    assert params["mime_type"] == "application/pdf"
    assert params["drive_id"] == "drive-9"


def test_explicit_declared_fields_win_over_locator_entries():
    request = ConnectorContentRequest(
        surface="files",
        mime_type="text/plain",
        locator={"mime_type": "application/pdf"},
    )

    assert request.to_params()["mime_type"] == "text/plain"


def test_content_request_from_a_node_without_a_handle_still_works():
    node = ConnectorNode.model_validate({"id": "n", "surface": "mail", "label": "Note"})

    request = ConnectorContentRequest.from_node(node)

    assert request.to_params() == {"surface": "mail"}
    assert node.fetchable is False
    assert node.content_locator() == {}


# --------------------------------------------------------------------------
# Path construction
# --------------------------------------------------------------------------


def test_surface_paths_are_scoped_to_the_requested_workroom():
    service, client = _service(
        {("GET", f"{_SURFACE_BASE}/browse"): {"items": []}}
    )

    service.browse_surface(
        _REF, ConnectorBrowseRequest(surface="files")
    )

    assert client.calls[0][1].startswith(
        f"/connectors/surfaces/workrooms/{_WORKROOM}/{_CONNECTOR}/"
    )


def test_surface_paths_percent_encode_their_segments():
    service, client = _service(
        {("GET", "/connectors/surfaces/workrooms/wr%2Fa/c%2Fb/browse"): {"items": []}}
    )

    service.browse_surface(
        ConnectorSurfaceRef(workroom_id="wr/a", connector_id="c/b"),
        ConnectorBrowseRequest(surface="files"),
    )

    assert client.calls[0][1] == "/connectors/surfaces/workrooms/wr%2Fa/c%2Fb/browse"


def test_surface_ref_accepts_uuid_objects_and_is_immutable():
    ref = ConnectorSurfaceRef(
        workroom_id=uuid.UUID(_WORKROOM), connector_id=uuid.UUID(_CONNECTOR)
    )

    assert ref.workroom_id == _WORKROOM
    assert ref.connector_id == _CONNECTOR
    with pytest.raises(ValueError):
        ref.connector_id = "other"


def test_surface_ref_rejects_an_empty_identifier():
    with pytest.raises(ValueError):
        ConnectorSurfaceRef(workroom_id=_WORKROOM, connector_id="")


def test_iter_surface_nodes_rejects_a_non_positive_page_bound_eagerly():
    """The ValueError must surface at the call, not at the first iteration."""
    service, _ = _service({})

    with pytest.raises(ValueError):
        service.iter_surface_nodes(
            _REF, ConnectorBrowseRequest(surface="files"), max_pages=0
        )


def test_served_filename_wins_over_the_callers_guess():
    class _Disposed(_Response):
        def __init__(self):
            super().__init__(b"x", "application/pdf")
            self.headers["content-disposition"] = 'attachment; filename="Q3%20Report.pdf"'

    service, _ = _service({("GET", f"{_SURFACE_BASE}/content/node-1"): _Disposed()})

    content = service.fetch_surface_content(
        _REF,
        "node-1",
        ConnectorContentRequest(surface="files", filename="guess.pdf"),
    )

    assert content.filename == "Q3 Report.pdf"


def test_caller_filename_is_used_when_the_platform_sends_none():
    service, _ = _service(
        {("GET", f"{_SURFACE_BASE}/content/node-1"): _Response(b"x", "application/pdf")}
    )

    content = service.fetch_surface_content(
        _REF, "node-1", ConnectorContentRequest(surface="files", filename="guess.pdf")
    )

    assert content.filename == "guess.pdf"


def test_empty_workroom_id_is_rejected_rather_than_building_a_bare_path():
    service, _ = _service({})

    with pytest.raises(ValueError):
        service.list_surface_catalog("   ")
