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

from tests.integration.test_tool_shed_lifecycle_live import (
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
    with pytest.raises(AssertionError, match="no JSON object"):
        _jsonrpc_envelope(
            "text/event-stream", [": keepalive", "data: ping", ""], "tool-abc"
        )
