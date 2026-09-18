"""The act-approval path: withheld from agents, and correct on the wire.

Two halves. The first asserts that none of the four act-approval methods
reaches the published surface, because an agent that can call ``resolve``
approves its own act and the gate becomes a formality. The second drives each
method through a real ``KamiwazaClient`` with the transport stubbed, so the
recorded requests are what the platform would receive — a service-level fake
cannot see which URL a method builds, which is where a second ``/api`` segment
would hide.
"""

from __future__ import annotations

import json
import warnings
from typing import Any
from urllib.parse import urlsplit

import pytest
import requests

from kamiwaza_sdk.agent_tools.ids import UNPUBLISHED
from kamiwaza_sdk.agent_tools.spec_index import build_index
from kamiwaza_sdk.client import KamiwazaClient

pytestmark = pytest.mark.unit

#: The four selectors of the act-approval path, as the index spells them.
ACT_APPROVAL_SELECTORS = (
    "console.act_approvals.create",
    "console.act_approvals.get",
    "console.act_approvals.resolve",
    "console.act_approvals.consume",
)

#: A digest is 64 hex characters, and the schemas hold the length.
_DIGEST = "a" * 64

_VIEW = {
    "id": "6f1f2b7c-6d36-4e4a-9d1f-0d3a1c2b4e5f",
    "status": "approved",
    "act_kind": "operation",
    "act_target": "deploy_model",
    "act_digest": _DIGEST,
    "title": "Deploy model llama-3 to the shared cluster",
    "act_payload": {"model": "llama-3"},
    "requester_id": "member-1",
    "created_at": "2026-03-01T12:30:00Z",
    "expires_at": "2026-03-08T12:30:00Z",
    "resolved_at": "2026-03-01T13:00:00Z",
    "resolver_id": "member-2",
    "consumed_at": None,
}

_OPERATION = {
    "id": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed",
    "kind": "act_approval",
    "status": "waiting_for_user",
    "origin": "sdk",
    "title": "Approval for deploy_model",
    "steps": [],
    "resource_refs": [
        {"kind": "act_approval", "id": "6f1f2b7c-6d36-4e4a-9d1f-0d3a1c2b4e5f"}
    ],
    "created_at": "2026-03-01T12:30:00Z",
}


@pytest.fixture(scope="module")
def client() -> KamiwazaClient:
    """A client built without a network, for the index walk."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return KamiwazaClient(base_url="http://localhost:7777/api")


@pytest.fixture(scope="module")
def index(client: KamiwazaClient):
    """The operation index, built from that client."""
    return build_index(client)


@pytest.mark.parametrize("op_selector", ACT_APPROVAL_SELECTORS)
def test_act_approval_operation_is_withheld_with_a_reason(index, op_selector) -> None:
    """No part of the approval path is an agent tool, and each says why.

    This is the check that stops a later refactor publishing an approval tool:
    ``resolve`` in the catalog lets an agent approve its own act, and ``create``
    and ``consume`` are the server's half of the handshake, reached on the
    caller's behalf.
    """
    reason = UNPUBLISHED.get(op_selector)
    assert reason is not None, f"{op_selector} is not in the withheld set"
    assert reason.reason.strip(), f"{op_selector} has no stated reason"

    entry = next(e for e in index if e.selector == op_selector)
    assert not entry.is_published
    published = {e.selector for e in index if e.is_published}
    assert op_selector not in published
    published_ids = {e.published_id for e in index if e.is_published}
    assert entry.published_id not in published_ids


def test_no_published_operation_reaches_the_approval_routes(index) -> None:
    """The whole ``console`` service stays off the published surface.

    Asserted over the service rather than the four names, so a fifth method
    added to the approval sub-API fails here instead of shipping published.
    """
    assert [
        e.selector for e in index if e.selector.startswith("console.") and e.is_published
    ] == []


def _http_response(
    request: requests.PreparedRequest, payload: Any, status: int
) -> requests.Response:
    """Build the response the stubbed transport hands back to the client."""
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    response.headers["Content-Type"] = "application/json"
    response.request = request
    response.url = request.url or ""
    return response


class StubTransport:
    """Answers ``requests.Session.send`` from a route table, recording calls.

    Attributes:
        seen: Every ``(method, path, body)`` sent, in order.
    """

    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        """Keep the route table and start with nothing recorded."""
        self._routes = routes
        self.seen: list[tuple[str, str, Any]] = []

    def __call__(
        self, request: requests.PreparedRequest, **kwargs: Any
    ) -> requests.Response:
        """Record one request and answer it, or 404 a path with no route."""
        path = urlsplit(request.url or "").path
        raw = request.body
        body = json.loads(raw) if isinstance(raw, (str, bytes)) else None
        method = request.method or ""
        self.seen.append((method, path, body))
        routed = self._routes.get((method, path))
        if routed is None:
            return _http_response(request, {"detail": f"no route for {path}"}, 404)
        status, payload = routed
        return _http_response(request, payload, status)


def _real_client(
    monkeypatch: pytest.MonkeyPatch, routes: dict[tuple[str, str], tuple[int, Any]]
) -> tuple[KamiwazaClient, StubTransport]:
    """Return a real client whose transport is the given route table."""
    transport = StubTransport(routes)
    monkeypatch.setattr(requests.Session, "send", transport)
    return (
        KamiwazaClient(base_url="http://localhost:7777/api", api_key="test-token"),
        transport,
    )


def test_create_posts_the_act_to_the_approval_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One POST to ``/api/act-approvals``, carrying the act and its digest.

    The client's base URL already ends in ``/api``, so a path written with a
    second ``/api`` would reach ``/api/api/act-approvals`` and 404 here.
    """
    client, transport = _real_client(
        monkeypatch, {("POST", "/api/act-approvals"): (202, _OPERATION)}
    )

    accepted = client.console.act_approvals.create(
        "operation",
        "deploy_model",
        _DIGEST,
        "Deploy model llama-3 to the shared cluster",
        {"model": "llama-3"},
        "deploy-llama-3-once",
        reason="The shared cluster has the memory for it",
        ttl_seconds=3600,
    )

    assert transport.seen == [
        (
            "POST",
            "/api/act-approvals",
            {
                "act_kind": "operation",
                "act_target": "deploy_model",
                "act_digest": _DIGEST,
                "title": "Deploy model llama-3 to the shared cluster",
                "act_payload": {"model": "llama-3"},
                "reason": "The shared cluster has the memory for it",
                "idempotency_key": "deploy-llama-3-once",
                "ttl_seconds": 3600,
            },
        )
    ]
    assert accepted.kind == "act_approval"


def test_create_omits_the_fields_it_was_not_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``reason`` and no ``ttl_seconds`` means neither reaches the body.

    Sending ``ttl_seconds: null`` would ask the platform to read a lifetime
    from a null rather than apply its own 7-day default.
    """
    client, transport = _real_client(
        monkeypatch, {("POST", "/api/act-approvals"): (202, _OPERATION)}
    )

    client.console.act_approvals.create(
        "workflow",
        "onboard_dataset",
        _DIGEST,
        "Onboard the quarterly dataset",
        {},
        "onboard-q1",
    )

    _, _, body = transport.seen[0]
    assert "reason" not in body
    assert "ttl_seconds" not in body
    assert body["act_kind"] == "workflow"


def test_get_reads_one_approval_by_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GET on the approval's own path, with the resolver in the result."""
    approval_id = _VIEW["id"]
    client, transport = _real_client(
        monkeypatch, {("GET", f"/api/act-approvals/{approval_id}"): (200, _VIEW)}
    )

    view = client.console.act_approvals.get(approval_id)

    assert transport.seen == [
        ("GET", f"/api/act-approvals/{approval_id}", None),
    ]
    assert view.resolver_id == "member-2"
    assert view.requester_id == "member-1"


def test_resolve_posts_the_decision_and_the_digest_it_was_shown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The decision and the presented digest both reach the resolve route.

    The digest is the binding: without it in the body the platform cannot
    refuse a decision taken on an act that has since changed.
    """
    approval_id = _VIEW["id"]
    client, transport = _real_client(
        monkeypatch,
        {("POST", f"/api/act-approvals/{approval_id}/resolve"): (200, _OPERATION)},
    )

    client.console.act_approvals.resolve(approval_id, "approved", _DIGEST)

    assert transport.seen == [
        (
            "POST",
            f"/api/act-approvals/{approval_id}/resolve",
            {"decision": "approved", "presented_digest": _DIGEST},
        )
    ]


def test_consume_posts_to_the_consume_route_with_no_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consuming names the approval in the path and sends nothing else."""
    approval_id = _VIEW["id"]
    consumed = dict(_VIEW, status="consumed", consumed_at="2026-03-01T13:05:00Z")
    client, transport = _real_client(
        monkeypatch,
        {("POST", f"/api/act-approvals/{approval_id}/consume"): (200, consumed)},
    )

    view = client.console.act_approvals.consume(approval_id)

    assert transport.seen == [
        ("POST", f"/api/act-approvals/{approval_id}/consume", None),
    ]
    assert view.status == "consumed"
    assert view.consumed_at is not None
