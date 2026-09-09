"""Offline evidence for otherwise-opaque failures in both federation readers."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
import requests
import responses

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.validation import federation_cases as fc
from tests.integration import _mini_clearance as mc

_SECRET = "private-token-and-record-content"


class _Response:
    status_code = 200
    headers = {"Content-Type": "text/event-stream; charset=utf-8"}
    url = f"https://peer.example/stream?token={_SECRET}"
    history: list = []

    def __init__(self, lines, error=None):
        self.lines = lines
        self.error = error
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self.closed = True

    def raise_for_status(self):
        return None

    def iter_lines(self, **_kwargs):
        yield from self.lines
        if self.error is not None:
            raise self.error


class _Persona:
    session = SimpleNamespace(verify=True)

    def __init__(self, job_status="FAILED"):
        self.job_status = job_status
        self.lookups = []
        self.job_response = None

    def _request(self, method, path, **kwargs):
        assert method == "POST"  # Diagnostics must bypass raw SDK error logging.
        assert kwargs["json"]["transport"] == "sse"
        return {"job_id": "job-1"}

    def lookup(self, url, **kwargs):
        self.lookups.append(("GET", url))
        assert kwargs["timeout"] == 10
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        assert kwargs["verify"] == self.session.verify
        assert kwargs["headers"] == {
            "Authorization": f"Bearer {_SECRET}",
            "X-Kamiwaza-Federation-Credential": _SECRET,
            "Accept": "application/json",
        }
        if isinstance(self.job_status, Exception):
            raise self.job_status
        self.job_response = _JobResponse(self.job_status)
        return self.job_response


class _JobResponse(_Response):
    def __init__(self, job_status):
        super().__init__([])
        self.job_status = job_status

    def json(self):
        return {"status": self.job_status, "transport": "sse", "secret": _SECRET}


@pytest.fixture(params=["provider", "paired"])
def invoke(request, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    for module in (fc, mc):
        monkeypatch.setattr(
            module,
            "federation_credential_headers",
            lambda _name: {"X-Kamiwaza-Federation-Credential": _SECRET},
        )

    def call(response, persona=None, *, real_http=False):
        persona = persona or _Persona()
        if not real_http:
            monkeypatch.setattr(
                "requests.get",
                lambda url, **kwargs: (
                    response
                    if url.endswith("/stream")
                    else persona.lookup(url, **kwargs)
                ),
            )
        if request.param == "provider":
            result = fc._mesh_retrieve(
                fc.RetrievalRequest(
                    persona, "https://source.example/api", _SECRET, "peer", "urn:test"
                )
            )
        else:
            result = mc.mesh_retrieve_through_gate(
                persona,
                "https://source.example/api",
                _SECRET,
                "peer",
                "urn:test",
                verify=persona.session.verify,
            )
        return result, persona

    return call


def _evidence(caplog, prefix):
    return [
        json.loads(record.getMessage().removeprefix(prefix))
        for record in caplog.records
        if record.getMessage().startswith(prefix)
    ]


@pytest.mark.parametrize("lines", [[], ["event: complete", "data: {}", ""]])
def test_empty_stream_retains_shape_and_terminal_job_state(invoke, caplog, lines):
    response = _Response(lines)
    result, persona = invoke(response)
    assert result == ([], [])  # Do not convert a failing known-answer into a pass.
    stream = _evidence(caplog, "federation_retrieval_stream ")[0]
    assert stream["http_status"] == 200
    assert stream["content_type"] == "sse"
    assert stream["eof"] is True
    assert stream["events"]["chunk"] == 0
    assert stream["events"]["complete"] == bool(lines)
    assert _evidence(caplog, "federation_retrieval_job ") == [
        {"lookup": "ok", "status": "FAILED", "transport": "sse"}
    ]
    assert persona.lookups == [
        ("GET", "https://source.example/api/mesh/peer/api/retrieval/jobs/job-1")
    ]
    assert response.closed
    assert persona.job_response.closed


@pytest.mark.parametrize("verify", [False, "/tmp/federation-test-ca.pem"])
def test_diagnostic_lookup_preserves_stream_tls_policy(invoke, verify):
    persona = _Persona()
    persona.session = SimpleNamespace(verify=verify)
    result, _ = invoke(_Response([]), persona)
    assert result == ([], [])
    assert persona.job_response.closed


def test_error_and_unknown_events_do_not_expose_contents(invoke, caplog):
    response = _Response(
        [
            "event: error",
            f'data: {{"detail": "{_SECRET}"}}',
            "",
            f"event: {_SECRET}",
            f"data: {_SECRET}",
            "",
        ]
    )
    response.headers = {"Content-Type": f"text/html; secret={_SECRET}"}
    response.history = [object()]
    invoke(response, _Persona(_SECRET))
    stream = _evidence(caplog, "federation_retrieval_stream ")[0]
    assert stream["events"]["error"] == 1
    assert stream["events"]["other"] == 1
    assert stream["content_type"] == "other"
    assert stream["redirects"] == 1
    assert _evidence(caplog, "federation_retrieval_job ")[0]["status"] == "unknown"
    assert _SECRET not in caplog.text


def test_failed_job_lookup_cannot_replace_original_stream_result(invoke, caplog):
    result, _ = invoke(_Response([]), _Persona(RuntimeError(_SECRET)))
    assert result == ([], [])
    assert _evidence(caplog, "federation_retrieval_job ") == [{"lookup": "failed"}]
    assert _SECRET not in caplog.text


@pytest.mark.parametrize("failure", ["http_500", "timeout", "redirect", "invalid_json"])
def test_real_client_diagnostic_failures_do_not_log_private_content(
    invoke, caplog, failure
):
    client = KamiwazaClient(base_url="https://source.example/api", api_key=_SECRET)
    job_url = "https://source.example/api/mesh/peer/api/retrieval/jobs/job-1"
    options = {
        "http_500": {"status": 500, "json": {"detail": _SECRET}},
        "timeout": {"body": requests.Timeout(_SECRET)},
        "redirect": {
            "status": 302,
            "headers": {"Location": f"https://elsewhere.example/{_SECRET}"},
        },
        "invalid_json": {"body": _SECRET, "content_type": "application/json"},
    }
    with responses.RequestsMock() as http:
        http.post(job_url.rsplit("/", 1)[0], json={"job_id": "job-1"})
        http.get(f"{job_url}/stream", body="", content_type="text/event-stream")
        http.get(job_url, **options[failure])
        result, _ = invoke(None, client, real_http=True)
        assert result == ([], [])
        assert _SECRET not in caplog.text
        assert _evidence(caplog, "federation_retrieval_job ")[0]["lookup"] == "failed"
        assert len(http.calls) == 3  # Never follow a redirect or retry the lookup.


@pytest.mark.parametrize("lines", [[], ["event: complete", "data: {}", ""]])
def test_incomplete_stream_diagnostics_survive_default_warning_logging(
    invoke, caplog, lines
):
    caplog.set_level(logging.WARNING)
    invoke(_Response(lines))
    assert _evidence(caplog, "federation_retrieval_stream ")[0]["eof"] is True


def test_none_lines_are_forwarded_without_diagnostic_failure(invoke):
    result, _ = invoke(_Response([None]))
    assert result == ([], [])


def test_stream_error_is_propagated_with_partial_diagnostics(invoke, caplog):
    error = RuntimeError(_SECRET)
    response = _Response(["event: chunk"], error)
    with pytest.raises(RuntimeError) as caught:
        invoke(response)
    assert caught.value is error
    stream = _evidence(caplog, "federation_retrieval_stream ")[0]
    assert stream["eof"] is False
    assert stream["events"]["chunk"] == 1
    assert _SECRET not in caplog.text
    assert response.closed


def test_success_preserves_rows_and_audits_without_extra_lookup(invoke, caplog):
    payload = {"data": [{"id": "r1"}], "metadata": {"gate_audit": {"filtered": True}}}
    response = _Response(
        [
            "event: chunk",
            f"data: {json.dumps(payload)}",
            "",
            "event: complete",
            "data: {}",
            "",
        ]
    )
    result, persona = invoke(response)
    assert result == (payload["data"], [payload["metadata"]["gate_audit"]])
    assert persona.lookups == []
    assert _evidence(caplog, "federation_retrieval_stream ")[0]["events"]["chunk"] == 1
    assert response.closed
