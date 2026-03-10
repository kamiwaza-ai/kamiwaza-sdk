from __future__ import annotations

import pytest

from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, VectorDBUnavailableError


pytestmark = pytest.mark.unit


class _StubResponse:
    def __init__(
        self,
        *,
        status_code: int,
        text: str = "",
        content_type: str = "application/json",
        json_data: object | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": content_type}
        self._json_data = json_data

    def json(self) -> object:
        if self._json_data is None:
            raise ValueError("No JSON payload")
        return self._json_data


def _make_client_with_response(
    monkeypatch: pytest.MonkeyPatch,
    response: _StubResponse,
) -> KamiwazaClient:
    client = KamiwazaClient(base_url="https://example.test/api")
    monkeypatch.setattr(client.session, "request", lambda *_args, **_kwargs: response)
    return client


def test_501_vectordb_path_maps_to_vectordb_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _StubResponse(
        status_code=501,
        text='{"detail":"no vector backend configured"}',
        json_data={"detail": "no vector backend configured"},
    )
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(VectorDBUnavailableError) as exc_info:
        client.get("/vectordb/collections")

    assert exc_info.value.status_code == 501
    assert exc_info.value.response_data == {"detail": "no vector backend configured"}


def test_501_context_vectordbs_path_maps_to_vectordb_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _StubResponse(
        status_code=501,
        text='{"detail":"no vector backend configured"}',
        json_data={"detail": "no vector backend configured"},
    )
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(VectorDBUnavailableError) as exc_info:
        client.get("/context/vectordbs")

    assert exc_info.value.status_code == 501
    assert exc_info.value.response_data == {"detail": "no vector backend configured"}


def test_501_non_vectordb_path_raises_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _StubResponse(
        status_code=501,
        text='{"detail":"feature not implemented"}',
        json_data={"detail": "feature not implemented"},
    )
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(APIError) as exc_info:
        client.get("/context/ontologies")

    assert not isinstance(exc_info.value, VectorDBUnavailableError)
    assert exc_info.value.status_code == 501
