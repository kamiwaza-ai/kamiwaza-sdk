"""Offline contracts for fail-closed mesh retrieval stream errors (ENG-9664).

This module deliberately has no live or integration marker. The live helper's
raw ``requests`` seam and mesh-outcome classification must stay observable on
every PR without a two-cluster rig.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import pytest

from kamiwaza_sdk.exceptions import APIError
from tests.integration import _mini_clearance as mc
from tests.integration import mesh_outcome
from tests.integration.test_federation_shared_idp_gated_retrieval_live import (
    _SHARED_IDP_POLICY,
)


class _PersonaClient:
    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, str]:
        assert method == "POST"
        assert path == "/mesh/peer/api/retrieval/jobs"
        assert kwargs == {"json": {"dataset_urn": "urn:test"}}
        return {"job_id": "job-1"}


class _StreamDenial:
    def __init__(self, content: bytes, *, status_code: int = 403) -> None:
        self.content = content
        self.status_code = status_code
        self.bytes_yielded = 0
        self.closed = False
        self.iter_content_calls = 0

    def __enter__(self) -> _StreamDenial:
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    @property
    def text(self) -> str:
        raise AssertionError("stream denials must not be eagerly materialized")

    def json(self) -> dict[str, Any]:
        raise AssertionError("stream denials must be decoded from capped bytes")

    def iter_content(self, *, chunk_size: int) -> Iterator[bytes]:
        self.iter_content_calls += 1
        for offset in range(0, len(self.content), chunk_size):
            chunk = self.content[offset : offset + chunk_size]
            self.bytes_yielded += len(chunk)
            yield chunk


def _invoke_stream_denial(
    monkeypatch: pytest.MonkeyPatch, response: _StreamDenial
) -> APIError:
    calls = 0

    def _get(url: str, **kwargs: Any) -> _StreamDenial:
        nonlocal calls
        calls += 1
        assert url == "https://source.example/mesh/peer/api/retrieval/jobs/job-1/stream"
        assert kwargs == {
            "headers": {
                "Authorization": "Bearer test-token",
                "Accept": "text/event-stream",
            },
            "stream": True,
            "verify": True,
            "timeout": 120,
        }
        return response

    monkeypatch.setattr("requests.get", _get)
    with pytest.raises(APIError) as exc_info:
        mc.mesh_retrieve_through_gate(
            _PersonaClient(),
            "https://source.example",
            "test-token",
            "peer",
            "urn:test",
            verify=True,
        )

    assert calls == 1
    assert response.closed
    assert response.iter_content_calls == 1
    return exc_info.value


def _failure(error: APIError) -> mesh_outcome.MeshFailure:
    return mesh_outcome._failure_from(error)


def test_eng_9664_structured_auth_denial_reaches_fail_closed_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "detail": {
            "reason": "peer_jwt_validation_failed",
            "error": "kid not present",
        }
    }
    response = _StreamDenial(json.dumps(body).encode("utf-8"))

    error = _invoke_stream_denial(monkeypatch, response)
    failure = _failure(error)

    assert error.response_data == body
    assert failure.reason == "peer_jwt_validation_failed"
    assert mesh_outcome.classify(failure, _SHARED_IDP_POLICY) == mesh_outcome.FAIL


def test_eng_9664_malformed_plaintext_uses_bounded_diagnostic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _StreamDenial(b"peer_jwt_validation_failed: invalid \xff{")

    error = _invoke_stream_denial(monkeypatch, response)
    failure = _failure(error)

    assert error.response_data is None
    assert "\ufffd" in (error.response_text or "")
    assert failure.reason is None
    assert mesh_outcome.classify(failure, _SHARED_IDP_POLICY) == mesh_outcome.FAIL


def test_eng_9664_oversized_body_is_capped_and_never_partially_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = mc._MESH_STREAM_ERROR_BODY_LIMIT_BYTES
    complete_json = json.dumps(
        {"detail": {"reason": "peer_jwt_validation_failed"}}
    ).encode("utf-8")
    capped_prefix = complete_json + (b" " * (limit - len(complete_json)))
    response = _StreamDenial(capped_prefix + (b"x" * (limit * 2)))

    error = _invoke_stream_denial(monkeypatch, response)
    diagnostic = error.response_text or ""
    retained = diagnostic.removesuffix(mc._MESH_STREAM_ERROR_TRUNCATION_MARKER)

    assert error.response_data is None
    assert diagnostic.endswith(mc._MESH_STREAM_ERROR_TRUNCATION_MARKER)
    assert retained.encode("utf-8") == capped_prefix
    assert response.bytes_yielded < len(response.content)
    assert (
        mesh_outcome.classify(_failure(error), _SHARED_IDP_POLICY) == mesh_outcome.FAIL
    )


def test_eng_9664_marker_free_truncated_denial_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = mc._MESH_STREAM_ERROR_BODY_LIMIT_BYTES
    response = _StreamDenial(b"x" * (limit * 3))

    error = _invoke_stream_denial(monkeypatch, response)
    failure = _failure(error)

    assert mesh_outcome.classify(failure, _SHARED_IDP_POLICY) == mesh_outcome.FAIL
    assert failure.response_truncated is True
    assert failure.reason is None


@pytest.mark.parametrize("status_code", [403, 404])
def test_eng_9664_complete_plain_downstream_denial_still_skips(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    response = _StreamDenial(
        b"downstream authorization denied", status_code=status_code
    )

    error = _invoke_stream_denial(monkeypatch, response)
    failure = _failure(error)

    assert failure.reason is None
    assert mesh_outcome.classify(failure, _SHARED_IDP_POLICY) == mesh_outcome.SKIP
