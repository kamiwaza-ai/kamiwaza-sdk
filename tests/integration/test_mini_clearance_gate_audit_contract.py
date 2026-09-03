"""Offline tripwires for the MiniClearance gate-audit live-test helpers.

This module deliberately has no live or integration marker.  The helpers drive
live suites, but their footer handling is deterministic and must run on every PR.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from kamiwaza_sdk.services.retrieval import RetrievalService
from tests.integration import _mini_clearance as mc


def _footer(*, filtered: bool, included: int | None = None) -> dict[str, Any]:
    return {
        "filtered": filtered,
        "included": included,
        "redacted": None,
        "total": None,
        "gate": None,
    }


def _chunk_lines(footers: list[Any]) -> list[str]:
    lines: list[str] = []
    for index, footer in enumerate(footers):
        payload = {
            "data": [{"row": index, "classification": "ALLOWED"}],
            "metadata": {"gate_audit": footer},
        }
        lines.extend(("event: chunk", f"data: {json.dumps(payload)}", ""))
    return lines


class _SSE:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def __enter__(self) -> _SSE:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        return None

    def iter_lines(self, *, decode_unicode: bool = False) -> Iterator[str | bytes]:
        for line in self.lines:
            yield line if decode_unicode else line.encode()


class _LocalHTTPClient:
    def __init__(self, lines: list[str]) -> None:
        self.response = _SSE(lines)

    def post(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        assert path == "/retrieval/jobs"
        return {
            "job_id": "job-1",
            "transport": "sse",
            "status": "RUNNING",
            "dataset": {"urn": json["dataset_urn"], "platform": "file"},
        }

    def get(self, path: str, **kwargs: Any) -> _SSE:
        assert path == "/retrieval/jobs/job-1/stream"
        assert kwargs == {"expect_json": False, "stream": True}
        return self.response


class _PersonaClient:
    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, str]:
        assert method == "POST"
        assert path == "/mesh/peer/api/retrieval/jobs"
        assert kwargs == {"json": {"dataset_urn": "urn:test", "transport": "sse"}}
        return {"job_id": "job-1"}


def test_local_helper_exposes_every_parsed_footer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    footers = [_footer(filtered=True, included=7), _footer(filtered=False)]
    retrieval = RetrievalService(_LocalHTTPClient(_chunk_lines(footers)))
    monkeypatch.setitem(mc.KNOWN, "TEST", (2, 1, {"ALLOWED"}))

    rows, gate_audits = mc.retrieve_through_gate(
        SimpleNamespace(retrieval=retrieval), "urn:test"
    )

    with pytest.raises(AssertionError, match="deprecated"):
        mc.assert_persona_result("TEST", rows, gate_audits)

    assert gate_audits == footers


def test_mesh_helper_preserves_every_parsed_footer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_key = _footer(filtered=True)
    missing_key.pop("included")
    footers = [missing_key, _footer(filtered=False)]
    response = _SSE(_chunk_lines([footers]))
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: response)
    monkeypatch.setitem(mc.KNOWN, "TEST", (1, 1, {"ALLOWED"}))

    rows, gate_audits = mc.mesh_retrieve_through_gate(
        _PersonaClient(),
        "https://source.example",
        "test-token",
        "peer",
        "urn:test",
        verify=True,
    )

    with pytest.raises(AssertionError, match="deprecated"):
        mc.assert_persona_result("TEST", rows, gate_audits)

    assert gate_audits == footers
