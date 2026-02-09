from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError


pytestmark = pytest.mark.unit


class _StubResponse:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload
        self.headers: Dict[str, str] = {"content-type": "application/json"}
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload


def test_put_schema_retries_after_recent_dataset_touch(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KamiwazaClient(base_url="https://example/api", api_key="dummy")

    dataset_urn = "urn:li:dataset:(urn:li:dataPlatform:file,/tmp/sdk,PROD)"
    client._note_recent_dataset_change(dataset_urn)

    responses: List[_StubResponse] = [
        _StubResponse(404, {"detail": "Dataset not found or schema could not be updated"}),
        _StubResponse(200, {"message": "ok"}),
    ]
    calls: List[tuple[str, str]] = []
    sleeps: List[float] = []

    def _request(method: str, url: str, **kwargs) -> _StubResponse:
        calls.append((method, url))
        return responses.pop(0)

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(client.session, "request", _request)
    monkeypatch.setattr("kamiwaza_sdk.client.time.sleep", _sleep)

    result = client.put(
        "/catalog/datasets/by-urn/schema",
        params={"urn": dataset_urn},
        json={"name": "sdk", "platform": "file", "fields": [{"name": "col", "type": "string"}]},
    )

    assert result == {"message": "ok"}
    assert len(calls) == 2
    assert len(sleeps) == 1


def test_put_schema_does_not_retry_for_unknown_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KamiwazaClient(base_url="https://example/api", api_key="dummy")

    dataset_urn = "urn:li:dataset:(urn:li:dataPlatform:file,/tmp/sdk,PROD)"

    calls: List[tuple[str, str]] = []

    def _request(method: str, url: str, **kwargs) -> _StubResponse:
        calls.append((method, url))
        return _StubResponse(404, {"detail": "Dataset not found or schema could not be updated"})

    monkeypatch.setattr(client.session, "request", _request)

    with pytest.raises(APIError) as exc:
        client.put(
            "/catalog/datasets/by-urn/schema",
            params={"urn": dataset_urn},
            json={"name": "sdk", "platform": "file", "fields": [{"name": "col", "type": "string"}]},
        )

    assert exc.value.status_code == 404
    assert len(calls) == 1

