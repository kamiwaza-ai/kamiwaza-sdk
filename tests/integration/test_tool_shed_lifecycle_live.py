"""Evidence for ``tools.mcp-tool-shed`` — deploy, health over MCP, discovery, stop.

ENG-12432. The capability document's planned SDK arm is one run: list available
templates, deploy one (supplying its required environment variables), assert the
deployment reports healthy over MCP and appears in discovery, then stop it. The
deploy-from-image path and the missing-required-variable failure mode are named
as natural assertions in the same run.

Two properties of the 1.2.1 surface shape this module, both checked against the
live deployment rather than assumed:

* **There is no purge for tool deployments.** ``stop_deployment`` is the only
  retirement path and it leaves a ``STOPPED`` row behind, unlike the App Garden
  surface which offers a purge. So this module's teardown asserts the
  deployment reached ``STOPPED``; it cannot assert its absence, and every run
  leaves one row.
* **Discovery is not filtered to running servers.** ``GET /tool/discover``
  returns every deployment record, whatever its status. This module therefore
  asserts that the deployment under test appears in discovery, and deliberately
  does NOT assert that discovery lists only running servers — see the module's
  entry in ``tests/e2e/capability_map.yaml`` for why that is left unclaimed
  rather than asserted either way.
* **The platform's health endpoint is not evidence.** It answers from the
  deployment's status field without contacting the tool, so MCP responsiveness
  is established here by a real ``initialize`` handshake instead.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterable, Iterator
from contextlib import suppress
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.tools import ToolTemplate

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

# Images served from a developer's local registry are not pullable by the
# cluster; a deploy failing on one says nothing about this capability.
LOCAL_DEV_REGISTRY_MARKERS = ("host.docker.internal", "localhost:", "127.0.0.1:")

# A protocol-level handshake. The server may negotiate a newer protocol
# version in its reply; the assertion is that it answers, not which version.
MCP_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "eng12432-evidence", "version": "0"},
    },
}

# Tools serve the streamable-HTTP protocol under this path; see the probe
# heuristics in ``kamiwaza_extensions/payload_builder.py``, which name ``/mcp``
# as the streamable-HTTP location and ``/sse`` as FastMCP's.
MCP_PATH = "/mcp"

# The two response media types MCP's streamable-HTTP transport defines. Compared
# on the essence -- the part before any ``;`` parameters -- rather than by
# substring: `text/plain; profile="application/json"` contains "json" while a
# conforming client rejects it, so a substring test would bless a response no
# real client would read.
JSON_MEDIA_TYPE = "application/json"
EVENT_STREAM_MEDIA_TYPE = "text/event-stream"

# Refusals the platform returns *before* starting a deploy, because of the
# template's administrative state on this instance rather than anything about the
# Tool Shed path. Both were observed on the evidence host on 2026-09-18, and each
# is matched on its status *and* a phrase from its message: matching a status
# class alone would walk past genuine deploy failures, which is the direction
# that turns a broken capability into a skipped one.
PRE_DEPLOY_REFUSALS = (
    # The selected release pins a Kamiwaza range excluding this instance:
    # "Extension 'tool-kamiwaza-dde' requires Kamiwaza '>=1.0.0,<1.2.0'; this
    # instance runs 1.2.1. Restore a compatible Kamiwaza version or ask an
    # administrator to choose a compatible extension release."
    (400, "requires Kamiwaza"),
    # An imported template shadowing a managed extension:
    # "Managed extension has a local shadow; an administrator must remove it"
    (409, "local shadow"),
)

DEPLOYED_STATUSES = frozenset({"DEPLOYED", "RUNNING"})
# Retirement is asserted positively, and only STOPPED counts. "not in
# DEPLOYED_STATUSES" would accept FAILED or PENDING as a clean stop, and
# STOP_REQUESTED proves only that the request was accepted — a workload that
# never actually stops would satisfy it while still running on a shared host.
RETIRED_STATUSES = frozenset({"STOPPED"})
SETTLED_STATUSES = frozenset({"DEPLOYED", "RUNNING", "FAILED", "STOPPED"})


def _skip_or_fail(reason: str) -> NoReturn:
    if os.environ.get("KZ_REQUIRE_TOOL_SHED_EVIDENCE") == "1":
        pytest.fail(reason)
    pytest.skip(reason)
    raise AssertionError("unreachable: pytest.skip always raises")


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _is_pre_deploy_refusal(error: APIError) -> bool:
    """Whether the platform declined to start the deploy over the template.

    Both recognised conditions -- a release whose Kamiwaza range excludes this
    instance, and an imported template shadowing a managed extension -- are
    properties of the template's administrative state, and the platform reports
    them before creating anything. So there is nothing to clean up and the next
    candidate can be tried, whereas any other error means the deploy itself
    failed and must surface.

    ``ToolTemplate`` carries no field for either condition -- verified against
    ``kamiwaza_sdk/schemas/tools.py``, which has no version-constraint or
    shadowed-managed-extension field -- so the catalogue gives the SDK nothing to
    pre-filter on and a refusal can only be recognised from the reply. That gap
    is recorded with the staged evidence.
    """
    return any(
        error.status_code == status and phrase in str(error)
        for status, phrase in PRE_DEPLOY_REFUSALS
    )


def _deployable_templates(client) -> list[ToolTemplate]:
    """Every imported template this suite could deploy, in listing order.

    ``KZ_TOOL_SHED_TEMPLATE`` pins a name explicitly. Otherwise take the
    imported templates whose image is not served from a developer's local
    registry and which declare no required environment variables, since this
    suite has no credentials to supply for one that does.

    A list rather than the first match: a template can also be refused at deploy
    time over its administrative state on the instance, which the SDK's catalogue
    does not expose (see ``_is_pre_deploy_refusal``). Returning one candidate made
    the whole capability unevidenceable the moment the first listed template
    pinned an incompatible Kamiwaza range -- which is a property of that
    template, not of the Tool Shed.
    """
    templates = client.tools.list_imported_templates()
    if not templates:
        _skip_or_fail("no imported tool templates on this host")

    pinned = os.environ.get("KZ_TOOL_SHED_TEMPLATE")
    if pinned:
        for template in templates:
            if template.name == pinned:
                return [template]
        _skip_or_fail(f"KZ_TOOL_SHED_TEMPLATE={pinned!r} is not imported here")

    rejected: list[str] = []
    candidates: list[ToolTemplate] = []
    for template in templates:
        image = template.image or ""
        if any(marker in image for marker in LOCAL_DEV_REGISTRY_MARKERS):
            rejected.append(f"{template.name} (local dev registry)")
            continue
        if template.required_env_vars:
            rejected.append(
                f"{template.name} (needs {', '.join(template.required_env_vars)})"
            )
            continue
        candidates.append(template)
    if candidates:
        return candidates

    _skip_or_fail("no imported template is deployable here: " + "; ".join(rejected))


def _wait_for_settled(client, deployment_id: UUID, *, timeout: float = 600) -> str:
    deadline = time.monotonic() + timeout
    status = client.tools.get_deployment(deployment_id).status
    while time.monotonic() < deadline:
        if status in SETTLED_STATUSES:
            return status
        time.sleep(10)
        status = client.tools.get_deployment(deployment_id).status
    # NOT _skip_or_fail: the deploy request was accepted, so failing to settle
    # is a failure of the mapped lifecycle, not an absent prerequisite.
    pytest.fail(
        f"tool deployment {deployment_id} never settled within {timeout}s "
        f"(last status {status!r})"
    )


@pytest.fixture
def stopped_tool_deployments(live_kamiwaza_client) -> Iterator[list[str]]:
    """Stop every tool deployment this test names, and prove each one settled.

    A fixture finalizer rather than a ``finally`` block: the name is registered
    before the creating call, so a deploy whose server side committed and whose
    response then raised is still reconciled; and a teardown failure is reported
    as its own ERROR instead of masking the test's failure or being masked by it.

    1.2.1 offers no purge for tool deployments, so retirement is proven by the
    status transition rather than by the row disappearing. The platform derives
    the row's name from the caller's -- it prefixes ``tool-`` -- so matching is
    on the run-unique token appearing anywhere in it.

    The closing assertion is an absence proof over a fresh listing, not a count
    of what this teardown happened to match. Requiring a match read a deploy the
    platform refused outright, which creates no row, as a teardown that stopped
    nothing; re-listing answers the question that actually matters -- whether any
    row carrying this run's token is still live -- and still catches a name the
    platform normalised beyond recognition, because such a row would show up
    here unretired.
    """
    client = live_kamiwaza_client
    names: list[str] = []
    yield names

    unretired: list[str] = []
    for name in names:
        for deployment in client.tools.list_deployments():
            if name not in str(deployment.name):
                continue
            with suppress(APIError):
                client.tools.stop_deployment(deployment.id)
            # Poll until STOPPED. A stop the platform applies asynchronously
            # would otherwise read as unretired on a healthy run, and breaking
            # on STOP_REQUESTED would accept "asked to stop" as "stopped".
            for _ in range(60):
                final = client.tools.get_deployment(deployment.id)
                if final.status in RETIRED_STATUSES:
                    break
                time.sleep(1)
            else:
                unretired.append(f"{deployment.name}={final.status}")

    # The delivery-site check: whatever this teardown did or did not match, no
    # row carrying one of this run's tokens may still be live. A name the
    # platform normalised past the matcher above lands here, and a run whose
    # deploy was refused outright has nothing to find, which is correct rather
    # than suspicious.
    leftovers = [
        f"{deployment.name}={deployment.status}"
        for deployment in client.tools.list_deployments()
        for name in names
        if name in str(deployment.name) and deployment.status not in RETIRED_STATUSES
    ]
    assert not unretired, (
        f"tool deployments did not reach a retired state and may still be "
        f"running on a shared host: {unretired}"
    )
    assert not leftovers, (
        f"tool deployments carrying this run's names are still live on a shared "
        f"host after teardown: {leftovers}"
    )


def test_tool_shed_template_catalog_is_listed(live_kamiwaza_client) -> None:
    """Available and imported catalogues are listed separately.

    Deliberately NOT mapped: a catalogue listing establishes nothing about
    deploying a tool server, and ENG-12432 rules out a catalog-only pass.
    """
    client = live_kamiwaza_client

    imported = client.tools.list_imported_templates()
    available = client.tools.list_available_templates()
    assert isinstance(imported, list)
    assert isinstance(available, list)
    assert imported, "no imported tool templates to report"

    garden = client.tools.get_garden_status()
    assert isinstance(garden, dict)


@pytest.mark.usefixtures("live_server_available")
def test_tool_shed_deploy_health_discovery_and_stop(
    live_kamiwaza_client, stopped_tool_deployments
) -> None:
    """The documented arm: deploy a template, prove MCP health, discover it, stop."""
    client = live_kamiwaza_client

    # The document's own flow for this capability is "list available templates,
    # deploy one", and names `list_available_templates` in the SDK surface it
    # claims. Exercised inside the *mapped* arm, not only in the unmapped
    # catalogue test, or the mapped record would stand while
    # `GET /tool/templates/available` was broken.
    available = client.tools.list_available_templates()
    assert isinstance(available, list), (
        f"list_available_templates returned {type(available).__name__}, not a list"
    )
    garden = client.tools.get_garden_status()
    assert isinstance(garden, dict), (
        "the document claims a garden status showing available versus imported; "
        f"get_garden_status returned {type(garden).__name__}"
    )

    candidates = _deployable_templates(client)

    pre_existing = {str(d.id) for d in client.tools.list_deployments()}
    refused: list[str] = []
    deployment = None
    for template in candidates:
        name = _unique("eng12432-tool")
        # Registered before the creating call, so a deploy whose server side
        # committed and whose response then raised is still reconciled by name.
        stopped_tool_deployments.append(name)
        try:
            deployment = client.tools.deploy_from_template(
                template_name=template.name,
                name=name,
            )
        except APIError as error:
            if not _is_pre_deploy_refusal(error):
                raise
            # Refused before anything was created, so there is nothing to clean
            # up and the next candidate is tried. Only a run where every
            # candidate is refused has no prerequisite to work with.
            refused.append(f"{template.name} ({error})")
            continue
        break
    if deployment is None:
        _skip_or_fail(
            "every deployable tool template was refused before deploy over its "
            "administrative state on this instance, so the Tool Shed capability "
            "has no fixture here: " + "; ".join(refused)
        )
    deployment_id = deployment.id

    assert str(deployment_id) not in pre_existing, (
        "deploy_from_template returned a deployment that already existed"
    )
    assert deployment.url, (
        "the deployment carries no public URL; the document promises a "
        "generated MCP endpoint"
    )
    _assert_public_https_url(deployment.url, name, "the deployment advertises")

    status = _wait_for_settled(client, deployment_id)
    assert status in DEPLOYED_STATUSES, (
        f"tool deployment {name} settled in {status!r}; the deploy station is "
        f"satisfied only by {sorted(DEPLOYED_STATUSES)}"
    )

    advertised_info = _assert_mcp_handshake(client, deployment.url, name)
    _assert_appears_in_discovery(client, deployment_id, name, advertised_info)

    # Stop is a station of this capability, so it is asserted here rather than
    # left to the finalizer, which suppresses errors by design so that it can
    # still run after a failed body.
    assert client.tools.stop_deployment(deployment_id), (
        f"stop_deployment({deployment_id}) did not report success for {name}"
    )

    # Retirement is asserted by the `stopped_tool_deployments` finalizer, which
    # reconciles by name and requires a status in RETIRED_STATUSES. Asserting it
    # inline would not run when the body fails, which is exactly when a leaked
    # workload on a shared host matters most.


def _assert_public_https_url(advertised_url: str, name: str, source: str) -> None:
    """The document's promise is a *stable HTTPS URL*, not any reachable address.

    "Each deployed tool server gets a stable HTTPS URL usable by any
    MCP-compatible client", and "Tool deployments receive stable HTTPS URLs behind
    the standard API gateway". A runner-reachable `http://` or pod-local address
    would satisfy a handshake while that promise was broken.
    """
    assert advertised_url.startswith("https://"), (
        f"the URL {source} for {name} is {advertised_url!r}; the capability "
        "promises a stable HTTPS URL usable by any MCP-compatible client"
    )


def _mcp_endpoint(advertised_url: str) -> str:
    """The MCP endpoint for a tool, derived from the URL the platform advertises.

    On the build this was captured against, ``ToolDeployment.url`` is the tool's
    service root and the path has to be appended -- inferred, not read off the
    wire: the handshake succeeds with ``/mcp`` appended, which it could not if
    the advertised URL were already the protocol path, since the gateway answers
    ``/mcp/mcp`` with its HTML catch-all.

    The schema, though, describes the field as "Public URL for the Tool server
    (MCP endpoint)", so a build that takes that literally is entitled to
    advertise the protocol path itself. Appending unconditionally would probe
    ``/mcp/mcp`` there and fail a healthy tool, so the suffix is added only when
    it is not already present.
    """
    base = advertised_url.rstrip("/")
    if base.endswith(MCP_PATH):
        return base
    return f"{base}{MCP_PATH}"


def _decoded_lines(response) -> Iterable[str]:
    """The reply's lines, decoded as UTF-8.

    Three conformance gaps are accepted here rather than parsed around, and named
    so nobody has to rediscover them:

    * ``iter_lines`` splits on ``str.splitlines()`` boundaries, which include
      U+2028, U+2029 and U+0085; SSE recognises only CR, LF and CRLF. A result
      carrying a raw U+2028 inside a string would be split mid-JSON and read as no
      response.
    * the streaming decoder replaces invalid UTF-8 rather than raising, so a
      broken byte inside ``serverInfo.name`` becomes U+FFFD and still parses.
    * a server that sends a priming event id and closes, expecting a GET
      reconnection with ``Last-Event-ID``, is reported as unanswered rather than
      resumed.

    Each would need this reader to become a full event-stream implementation, and
    every round of hardening in this area has introduced its own defect. The
    shortfall is the honest trade: an endpoint doing any of the three fails this
    handshake and the failure is legible, rather than a passing record resting on a
    parser nobody has exercised against a real server.

    Both media types MCP defines are UTF-8, but ``requests`` derives the encoding
    from the headers, and for ``text/event-stream`` without a charset parameter
    that yields ISO-8859-1 (verified against requests 2.34.2:
    ``get_encoding_from_headers({"content-type": "text/event-stream"})`` returns
    ``'ISO-8859-1'``). A permitted UTF-8 byte-order mark would then arrive as
    ``ï»¿`` rather than ``\ufeff``, the first field name would not read as
    ``data``, and a conforming endpoint would fail this handshake. Any non-ASCII
    payload would be mojibake for the same reason.
    """
    response.encoding = "utf-8"
    return response.iter_lines(decode_unicode=True)


def _reject_non_json_constant(token: str) -> object:
    """RFC 8259 has no NaN or Infinity; Python's decoder accepts both.

    A reply carrying one is not JSON, and a conforming client rejects it, so the
    handshake must too rather than parsing it into a float.
    """
    raise AssertionError(
        f"the reply carries the non-JSON constant {token!r}; RFC 8259 defines no "
        "such literal, so this is not a JSON-RPC message a client can read"
    )


def _decode_event(fields: list[str]) -> object | None:
    """One server-sent event's data fields, decoded, or None if not JSON.

    A keepalive or a partial event is not an error to raise on -- the reader
    moves to the next event -- so an undecodable payload comes back as None.
    """
    if not fields:
        return None
    try:
        return json.loads("\n".join(fields), parse_constant=_reject_non_json_constant)
    except json.JSONDecodeError:
        return None


def _is_jsonrpc_response(frame: object) -> bool:
    """Whether a decoded stream frame is the answer to a call.

    The stream a POST is answered on may carry server-to-client notifications
    and requests before the response, so returning the first JSON object would
    hand back a notification and fail the caller's id correlation against a
    perfectly healthy server. A response is the frame that answers: it carries
    ``result`` or ``error`` and, unlike a server request, no ``method``.

    Deliberately does not look at the request id. Selecting the frame by the id
    the caller is about to assert on would make that assertion tautological.
    """
    return (
        isinstance(frame, dict)
        and "method" not in frame
        and ("result" in frame or "error" in frame)
    )


def _jsonrpc_envelope(content_type: str, body_lines: Iterable[str], name: str) -> dict:
    """The JSON-RPC envelope from an MCP streamable-HTTP reply.

    The transport lets the server answer either as ``application/json`` or as a
    ``text/event-stream`` frame, and this client's ``Accept`` header invites
    both, so rejecting the stream form would fail a compliant server.

    What must still be rejected is the gateway's HTML catch-all: it answers an
    unknown path under a tool's route with 200 and the dashboard page rather than
    a 404, so an HTTP 200 alone proves nothing about what is serving the path.

    Takes the decoded lines rather than the response so the parsing is testable
    without a cluster; ``tests/unit/test_tool_shed_mcp_helpers.py`` exercises
    both transports and the catch-all.
    """
    transport = content_type.split(";", 1)[0].strip().lower()
    if transport == JSON_MEDIA_TYPE:
        return json.loads(
            "\n".join(body_lines), parse_constant=_reject_non_json_constant
        )
    if transport == EVENT_STREAM_MEDIA_TYPE:
        # An event's consecutive ``data:`` fields are one payload joined by
        # newlines, and the blank line dispatches the event (HTML standard,
        # server-sent events). Parsing each line on its own would fail both
        # halves of a response the server split across two fields and report a
        # healthy tool as never having answered.
        #
        # A stream that ends mid-event is NOT dispatched, because the standard
        # discards pending data at end of stream: a conforming client receives no
        # response from such an endpoint, so accepting one here would publish a
        # passing record for a transport that answers nobody. Strictness costs a
        # false failure only for a server that is already non-conforming.
        fields: list[str] = []
        for index, raw in enumerate(body_lines):
            # One leading byte-order mark is permitted on the stream and must be
            # stripped, or the first field name reads as "\ufeffdata".
            # Exactly one, which is what the standard strips: a second BOM stays
            # attached to the field name, so `data` is not recognised -- and a
            # conforming client would not recognise it either.
            line = raw.removeprefix("\ufeff") if index == 0 else raw
            # A CR survives when the stream uses CRLF and the reader split on LF.
            line = line[:-1] if line.endswith("\r") else line
            if line.startswith("data:"):
                value = line[len("data:") :]
                # The spec strips one optional space after the colon. Kept for
                # faithfulness, not pinned by a test: JSON ignores whitespace,
                # so no payload can tell the two spellings apart.
                fields.append(value[1:] if value.startswith(" ") else value)
                continue
            if line != "":
                # Only an *empty* line dispatches. A whitespace-only line is an
                # unknown field, which is ignored -- treating it as a terminator
                # would dispatch an event the standard leaves pending, and accept
                # a stream no conforming client reads an answer from.
                #
                # Comment lines (":") and the event/id/retry fields carry no
                # payload either. Only ``data:`` can.
                continue
            frame = _decode_event(fields)
            fields = []
            if _is_jsonrpc_response(frame):
                return frame
        raise AssertionError(
            f"the event-stream reply from {name} carried no JSON-RPC response in "
            "any dispatched event, so the initialize call was never answered. An "
            "event the stream left pending at end of stream does not count: the "
            "standard discards it, so no conforming client would see it either"
        )
    raise AssertionError(
        f"MCP initialize against {name} returned media type {transport!r} "
        f"(content-type {content_type!r}), which is neither "
        f"{JSON_MEDIA_TYPE!r} nor {EVENT_STREAM_MEDIA_TYPE!r}. A conforming MCP "
        f"client rejects it; the gateway answers an unknown path under a tool's "
        f"route with 200 and dashboard HTML, so nothing is serving {MCP_PATH} at "
        "the advertised URL"
    )


def _assert_mcp_handshake(client, advertised_url: str, name: str) -> dict:
    """Prove the tool answers MCP at the URL the platform advertises.

    Returns the ``serverInfo`` it proved, so a caller can establish that a second
    URL belongs to the same tool rather than to another healthy one.

    Deliberately NOT via ``GET /tool/deployment/{id}/health``. That endpoint
    returns ``healthy`` whenever the deployment row reads ``DEPLOYED`` and
    reports a hardcoded ``protocol_version``; it never contacts the tool. Since
    this test has already polled until the deployment reached ``DEPLOYED``,
    asserting on it would be bounded by construction and could not fail.

    ``protocolVersion`` and ``serverInfo`` cannot be derived from a status
    field, so a handshake that returns them is evidence the endpoint is a live
    MCP server. Going through the advertised URL rather than a pod address also
    exercises the document's actual guarantee: a stable HTTPS URL usable by any
    MCP-compatible client.
    """
    url = _mcp_endpoint(advertised_url)
    if client.authenticator is not None:
        client.authenticator.authenticate(client.session)
    # Streamed so an event-stream reply can be read frame by frame: the envelope
    # is taken from the first data: frame instead of waiting for a stream the
    # server is entitled to hold open, which would otherwise stall until the
    # read timeout.
    with client.session.post(
        url,
        json=MCP_INITIALIZE,
        headers={"Accept": "application/json, text/event-stream"},
        timeout=60,
        stream=True,
    ) as response:
        assert response.status_code == 200, (
            f"MCP initialize against {name} at {url} returned HTTP "
            f"{response.status_code}"
        )
        body = _jsonrpc_envelope(
            response.headers.get("content-type", ""),
            _decoded_lines(response),
            name,
        )
    # Without these two, an uncorrelated body carrying the right-looking keys
    # would pass as a handshake.
    assert body.get("jsonrpc") == "2.0", (
        f"the reply from {name} is not a JSON-RPC 2.0 envelope: {body}"
    )
    # `True == 1` in Python, and JSON `true` decodes to `True`, so an equality
    # test alone accepts `"id": true` -- which is not a JSON-RPC id at all.
    reply_id = body.get("id")
    assert isinstance(reply_id, int) and not isinstance(reply_id, bool), (
        f"the reply from {name} carries id {reply_id!r} ({type(reply_id).__name__}); "
        "a JSON-RPC id answering this request is a number"
    )
    assert reply_id == MCP_INITIALIZE["id"], (
        f"the reply from {name} does not correlate with the request id "
        f"{MCP_INITIALIZE['id']!r}: {body}"
    )
    # Deliberately `is None` rather than "error must be absent". JSON-RPC 2.0 does
    # forbid a response carrying both members, but the Kamiwaza tool this arm
    # deployed on 2026-09-18 answered
    #   {"jsonrpc":"2.0","id":1,"result":{...},"error":null}
    # -- result plus an explicit null error. Enforcing the letter of the spec here
    # would fail a server that works, which is the one thing an evidence test must
    # not do. The platform's spelling is noted with the staged evidence instead.
    assert body.get("error") is None, (
        f"MCP initialize against {name} returned a JSON-RPC error: {body.get('error')}"
    )
    result = body.get("result") or {}
    _assert_initialize_result(result, name)
    return result["serverInfo"]


# An MCP protocol version is a dated revision, e.g. "2024-11-05". The live
# handshake this arm recorded on 2026-09-18 answered "2025-11-25".
PROTOCOL_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _assert_initialize_result(result: dict, name: str) -> None:
    """The result is an MCP InitializeResult, not merely a dict with two keys.

    ``initialize`` answers with ``protocolVersion``, ``capabilities`` and a
    ``serverInfo`` carrying both ``name`` and ``version``. Asserting only that
    ``protocolVersion`` and ``serverInfo.name`` are truthy accepts
    ``{"protocolVersion": "garbage", "serverInfo": {"name": "stub"}}`` -- a shape
    no conforming client can use -- and this arm's whole claim is that the
    endpoint speaks the protocol.

    A seam rather than inline assertions so the shapes can be driven directly;
    the positive control in the unit module is the handshake the live server
    actually returned.
    """
    version = result.get("protocolVersion")
    assert isinstance(version, str) and PROTOCOL_VERSION_RE.match(version), (
        f"the MCP result for {name} carries protocolVersion {version!r}, which is "
        "not a dated protocol revision"
    )
    capabilities = result.get("capabilities")
    assert isinstance(capabilities, dict), (
        f"the MCP result for {name} carries capabilities {capabilities!r}; "
        "initialize must declare what the server supports"
    )
    server_info = result.get("serverInfo")
    assert isinstance(server_info, dict), (
        f"the MCP result for {name} carries serverInfo {server_info!r}"
    )
    for field in ("name", "version"):
        value = server_info.get(field)
        assert isinstance(value, str) and value.strip(), (
            f"the MCP result for {name} carries serverInfo.{field} {value!r}; "
            "an Implementation declares both a name and a version"
        )


def _assert_appears_in_discovery(
    client, deployment_id: UUID, name: str, advertised_info: dict
) -> None:
    """The deployment under test is discoverable by any MCP-capable client."""
    discovery = client.tools.discover_servers()
    assert discovery.total == len(discovery.servers), (
        f"discovery reported total={discovery.total} but listed "
        f"{len(discovery.servers)} servers"
    )
    mine = [s for s in discovery.servers if str(s.deployment_id) == str(deployment_id)]
    assert len(mine) == 1, (
        f"the deployment under test appears {len(mine)} times in discovery; "
        "expected exactly once"
    )
    assert mine[0].url, "the discovered server carries no URL for a client to call"
    _assert_public_https_url(mine[0].url, name, "discovery publishes")
    # Non-empty is not usable. Discovery is the route a third-party MCP client
    # takes, so the endpoint it publishes is handshaken in its own right: a stale
    # or wrong URL here passes every other assertion while no client can reach
    # the tool.
    discovered_info = _assert_mcp_handshake(
        client, mine[0].url, f"{name} (as published by discovery)"
    )
    # ...and it must be the SAME tool. A row carrying the right deployment id and
    # another healthy tool's URL would otherwise pass, while discovery routed
    # clients to the wrong deployment.
    assert discovered_info == advertised_info, (
        f"discovery publishes a URL whose server identifies as {discovered_info}, "
        f"while the deployment's own URL answers as {advertised_info}; discovery "
        "routes clients to a different tool"
    )


@pytest.mark.usefixtures("live_server_available")
def test_tool_shed_template_deploy_names_missing_required_env_vars(
    live_kamiwaza_client, stopped_tool_deployments
) -> None:
    """A template deploy missing a required variable names the variable.

    The document says this "fails as a client error naming the missing
    variables, not as a broken deployment". The assertion is on the **message**
    rather than the status code, because ``ToolService.deploy_from_template``
    re-raises this case as ``APIError(str(e))`` — a fresh exception built from
    the message alone, which discards ``status_code``. Asserting a 400 here
    would fail on the correct path.

    Deliberately NOT mapped on its own: it is a negative path, and under the
    PER-TEST RULE a run of it alone must never emit a passing record for the
    capability.
    """
    client = live_kamiwaza_client

    # Only an IMPORTED template is deployable: deploy_from_template resolves
    # against the imported catalogue, and an available-but-unimported name
    # comes back as "Template <name> not found". Importing one to create this
    # fixture would mutate the shared host's catalogue, which this suite does
    # not do, so an absent fixture is a skip with its reason named.
    candidates = [
        t for t in client.tools.list_imported_templates() if t.required_env_vars
    ]
    if not candidates:
        pytest.skip(
            "no IMPORTED tool template declares required_env_vars on this host, "
            "so the documented missing-variable failure mode cannot be "
            "exercised without importing one and mutating the shared catalogue"
        )
    template = candidates[0]
    required = template.required_env_vars[0]

    # Registered before the call, even though the call is expected to fail: if
    # the platform regresses and accepts the deploy, pytest.raises fails here
    # while discarding the returned deployment, so only a name registered in
    # advance can reconcile the workload off this shared host.
    name = _unique("eng12432-missingenv")
    stopped_tool_deployments.append(name)

    with pytest.raises(APIError) as exc:
        client.tools.deploy_from_template(
            template_name=template.name,
            name=name,
        )

    message = str(exc.value)
    assert "Missing required environment variables" in message, (
        f"the error should identify a missing-variable rejection; got {message[:200]}"
    )
    assert required in message, (
        f"the error should name the missing variable {required!r}; got {message[:200]}"
    )
