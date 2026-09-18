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

    status = _wait_for_settled(client, deployment_id)
    assert status in DEPLOYED_STATUSES, (
        f"tool deployment {name} settled in {status!r}; the deploy station is "
        f"satisfied only by {sorted(DEPLOYED_STATUSES)}"
    )

    _assert_mcp_handshake(client, deployment.url, name)
    _assert_appears_in_discovery(client, deployment_id, name)

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
    transport = content_type.lower()
    if "json" in transport:
        return json.loads("\n".join(body_lines))
    if "text/event-stream" in transport:
        for line in body_lines:
            if not line.startswith("data:"):
                # Comment lines (":") and the event/id fields carry no payload,
                # and a keepalive frame is not the reply. Only `data:` can be.
                continue
            payload = line[len("data:") :].strip()
            try:
                frame = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if _is_jsonrpc_response(frame):
                return frame
        raise AssertionError(
            f"the event-stream reply from {name} carried no JSON-RPC response in "
            "any data: frame, so the initialize call was never answered"
        )
    raise AssertionError(
        f"MCP initialize against {name} returned content-type {content_type!r}; "
        "the gateway fell through to its HTML catch-all, so nothing is serving "
        f"{MCP_PATH} at the advertised URL"
    )


def _assert_mcp_handshake(client, advertised_url: str, name: str) -> None:
    """Prove the tool answers MCP at the URL the platform advertises.

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
            response.iter_lines(decode_unicode=True),
            name,
        )
    # Without these two, an uncorrelated body carrying the right-looking keys
    # would pass as a handshake.
    assert body.get("jsonrpc") == "2.0", (
        f"the reply from {name} is not a JSON-RPC 2.0 envelope: {body}"
    )
    assert body.get("id") == MCP_INITIALIZE["id"], (
        f"the reply from {name} does not correlate with the request id "
        f"{MCP_INITIALIZE['id']!r}: {body}"
    )
    assert body.get("error") is None, (
        f"MCP initialize against {name} returned a JSON-RPC error: {body.get('error')}"
    )
    result = body.get("result") or {}
    assert result.get("protocolVersion"), (
        f"the MCP result for {name} carries no protocolVersion: {body}"
    )
    server_info = result.get("serverInfo") or {}
    assert server_info.get("name"), (
        f"the MCP result for {name} carries no serverInfo.name: {body}"
    )


def _assert_appears_in_discovery(client, deployment_id: UUID, name: str) -> None:
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
    # Non-empty is not usable. Discovery is the route a third-party MCP client
    # takes, so the endpoint it publishes is handshaken in its own right: a stale
    # or wrong URL here passes every other assertion while no client can reach
    # the tool.
    _assert_mcp_handshake(client, mine[0].url, f"{name} (as published by discovery)")


@pytest.mark.usefixtures("live_server_available")
def test_tool_shed_template_deploy_names_missing_required_env_vars(
    live_kamiwaza_client,
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

    with pytest.raises(APIError) as exc:
        client.tools.deploy_from_template(
            template_name=template.name,
            name=_unique("eng12432-missingenv"),
        )

    message = str(exc.value)
    assert "Missing required environment variables" in message, (
        f"the error should identify a missing-variable rejection; got {message[:200]}"
    )
    assert required in message, (
        f"the error should name the missing variable {required!r}; got {message[:200]}"
    )
