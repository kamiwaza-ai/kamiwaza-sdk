"""Unit cover for the Tool Shed evidence module's MCP helpers.

ENG-12432. Both helpers exist to keep a *false failure* off a mapped evidence
record: the endpoint derivation so a build that advertises the protocol path
itself is not probed at ``/mcp/mcp``, and the envelope extraction so a server
that answers over ``text/event-stream`` -- a transport this client's ``Accept``
header invites -- is not rejected as if nothing were serving the path.

Neither case can be reached on the evidence host, whose build advertises a
service root and answers with JSON, so they are exercised here instead of left
as branches no run has ever executed. Follows the convention in
``test_live_model_file_cleanup_contract.py``: a live module's helpers are
imported and driven directly.
"""

import pytest

from kamiwaza_sdk.exceptions import APIError
from tests.integration.test_tool_shed_lifecycle_live import (
    _is_pre_deploy_refusal,
    _jsonrpc_envelope,
    _mcp_endpoint,
)

ENVELOPE = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}


def test_mcp_endpoint_appends_the_protocol_path_to_a_service_root() -> None:
    assert _mcp_endpoint("https://host/tool-abc") == "https://host/tool-abc/mcp"
    assert _mcp_endpoint("https://host/tool-abc/") == "https://host/tool-abc/mcp"


def test_mcp_endpoint_leaves_an_advertised_protocol_path_alone() -> None:
    """The schema calls ``ToolDeployment.url`` the MCP endpoint.

    A build that takes it literally must not be probed at ``/mcp/mcp``, which
    the gateway answers with its HTML catch-all -- failing a healthy tool.
    """
    assert _mcp_endpoint("https://host/tool-abc/mcp") == "https://host/tool-abc/mcp"
    assert _mcp_endpoint("https://host/tool-abc/mcp/") == "https://host/tool-abc/mcp"


def test_mcp_endpoint_does_not_treat_a_lookalike_path_as_the_endpoint() -> None:
    assert _mcp_endpoint("https://host/mcp-server") == "https://host/mcp-server/mcp"


def test_jsonrpc_envelope_reads_a_json_reply() -> None:
    body = ['{"jsonrpc": "2.0", "id": 1,', '"result": {"protocolVersion": "1"}}']
    assert _jsonrpc_envelope("application/json", body, "tool-abc") == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"protocolVersion": "1"},
    }


def test_jsonrpc_envelope_reads_an_event_stream_reply() -> None:
    """A compliant streamable-HTTP server may answer over SSE.

    The frame is preceded by the fields and keepalive comment a real stream
    carries, none of which is the payload.
    """
    body = [
        ": keepalive",
        "event: message",
        "id: 1",
        'data: {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}',
        "",
    ]
    assert _jsonrpc_envelope("text/event-stream; charset=utf-8", body, "tool-abc") == (
        ENVELOPE
    )


def test_jsonrpc_envelope_rejects_the_gateways_html_catch_all() -> None:
    """The reason the content type is inspected at all.

    An unknown path under a tool's route is answered with 200 and the dashboard
    page, so the status code alone establishes nothing.
    """
    with pytest.raises(AssertionError, match="HTML catch-all"):
        _jsonrpc_envelope("text/html; charset=utf-8", ["<!doctype html>"], "tool-abc")


def test_jsonrpc_envelope_rejects_an_event_stream_carrying_no_envelope() -> None:
    with pytest.raises(AssertionError, match="no JSON-RPC response"):
        _jsonrpc_envelope(
            "text/event-stream", [": keepalive", "data: ping", ""], "tool-abc"
        )


# Verbatim from the evidence host on 2026-09-18, when the selected release of
# tool-kamiwaza-dde began pinning a range that excludes the 1.2.1 instance.
VERSION_REFUSAL = (
    'API request failed with status 400: {"detail":"Extension '
    "'tool-kamiwaza-dde' requires Kamiwaza '>=1.0.0,<1.2.0'; this instance runs "
    "1.2.1. Restore a compatible Kamiwaza version or ask an administrator to "
    "choose a compatible extension release. Selection changes apply to new "
    'deployments; existing deployments remain unchanged."}'
)


def test_a_version_gated_deploy_is_recognised_as_a_pre_deploy_refusal() -> None:
    """The refusal the candidate walk has to tell apart from a real failure."""
    assert _is_pre_deploy_refusal(APIError(VERSION_REFUSAL, status_code=400))


def test_another_bad_request_is_not_a_version_refusal() -> None:
    """A 400 the test must still raise on, not skip past to the next template."""
    error = APIError(
        'API request failed with status 400: {"detail":"name already in use"}',
        status_code=400,
    )
    assert not _is_pre_deploy_refusal(error)


def test_a_server_error_quoting_the_sentence_is_not_a_version_refusal() -> None:
    """The status code is checked, so the message alone cannot excuse a 500."""
    assert not _is_pre_deploy_refusal(APIError(VERSION_REFUSAL, status_code=500))


def test_a_local_error_carrying_no_status_is_not_a_version_refusal() -> None:
    assert not _is_pre_deploy_refusal(APIError(VERSION_REFUSAL))


def test_a_bad_request_merely_mentioning_kamiwaza_is_not_a_version_refusal() -> None:
    """The needle is the phrase, not the product name.

    Constructed rather than observed, and the more important of the two negative
    cases: matching on "Kamiwaza" alone would let any Kamiwaza-worded 400 -- a
    genuine deploy failure -- be walked past as though the template were merely
    version-gated, turning a broken capability into a skipped one.
    """
    detail = "Extension 'tool-x' image is not present in the Kamiwaza registry"
    error = APIError(
        'API request failed with status 400: {"detail":"' + detail + '"}',
        status_code=400,
    )
    assert not _is_pre_deploy_refusal(error)


# Verbatim from the evidence host on 2026-09-18: the other template that passes
# the pullable-image and no-secrets filters is refused for a different reason.
SHADOW_REFUSAL = (
    'API request failed with status 409: {"detail":"Managed extension has a '
    'local shadow; an administrator must remove it"}'
)


def test_a_shadowed_managed_extension_is_recognised_as_a_pre_deploy_refusal() -> None:
    assert _is_pre_deploy_refusal(APIError(SHADOW_REFUSAL, status_code=409))


def test_a_conflict_for_another_reason_is_not_a_pre_deploy_refusal() -> None:
    """A 409 the arm must raise on rather than walk past."""
    error = APIError(
        'API request failed with status 409: {"detail":"a deployment with that '
        'name already exists"}',
        status_code=409,
    )
    assert not _is_pre_deploy_refusal(error)


def test_the_shadow_phrase_at_another_status_is_not_a_pre_deploy_refusal() -> None:
    """Each refusal is recognised by status and phrase together, not either."""
    assert not _is_pre_deploy_refusal(APIError(SHADOW_REFUSAL, status_code=400))


def test_jsonrpc_envelope_skips_a_notification_sent_before_the_response() -> None:
    """A compliant server may send notifications on the stream first.

    Returning the first JSON object would hand back the notification, whose id
    does not correlate, and fail a healthy server. The response is the frame
    carrying result or error and no method.
    """
    body = [
        'data: {"jsonrpc": "2.0", "method": "notifications/message", '
        '"params": {"level": "info"}}',
        'data: {"jsonrpc": "2.0", "id": 99, "method": "roots/list"}',
        'data: {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}',
    ]
    assert _jsonrpc_envelope("text/event-stream", body, "tool-abc") == ENVELOPE


def test_jsonrpc_envelope_returns_an_error_response() -> None:
    """A JSON-RPC error is an answer; the caller asserts on it, not the parser."""
    body = ['data: {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601}}']
    envelope = _jsonrpc_envelope("text/event-stream", body, "tool-abc")
    assert envelope["error"] == {"code": -32601}


def test_jsonrpc_envelope_rejects_a_stream_of_notifications_only() -> None:
    with pytest.raises(AssertionError, match="no JSON-RPC response"):
        _jsonrpc_envelope(
            "text/event-stream",
            ['data: {"jsonrpc": "2.0", "method": "notifications/message"}'],
            "tool-abc",
        )
