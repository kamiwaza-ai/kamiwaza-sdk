from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError
from test_support.workroom_isolation import (
    CONNECTOR_LIST_RETRY_DELAYS_SECONDS,
    list_connectors,
)

pytestmark = pytest.mark.unit


class _ConnectorListClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class _ManualClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.value += delay


def test_retries_transient_blank_403_before_success():
    client = _ConnectorListClient(
        [
            APIError("forbidden", status_code=403, response_text=""),
            {"items": [{"id": "connector-1"}]},
        ]
    )
    sleeps = []

    result = list_connectors(
        client,
        "wr-1",
        retry_delays=(0.25,),
        sleep=sleeps.append,
    )

    assert result == [{"id": "connector-1"}]
    assert sleeps == [0.25]
    assert client.calls == [
        ("/dde/connectors/", {"headers": {"X-Workroom-Id": "wr-1"}}),
        ("/dde/connectors/", {"headers": {"X-Workroom-Id": "wr-1"}}),
    ]


def test_retries_whitespace_only_403_before_success():
    client = _ConnectorListClient(
        [
            APIError("forbidden", status_code=403, response_text="\n  "),
            {"items": [{"id": "connector-1"}]},
        ]
    )
    sleeps = []

    result = list_connectors(
        client,
        "wr-1",
        retry_delays=(0.25,),
        sleep=sleeps.append,
    )

    assert result == [{"id": "connector-1"}]
    assert sleeps == [0.25]
    assert len(client.calls) == 2


def test_raises_diagnostic_after_default_retry_budget():
    errors = [
        APIError("forbidden", status_code=403, response_text="")
        for _ in range(len(CONNECTOR_LIST_RETRY_DELAYS_SECONDS) + 1)
    ]
    client = _ConnectorListClient(errors)
    clock = _ManualClock()

    with pytest.raises(AssertionError) as exc_info:
        list_connectors(
            client,
            "wr-2",
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

    message = str(exc_info.value)
    assert "X-Workroom-Id='wr-2'" in message
    assert "after 6 attempts" in message
    assert "over 10.0s" in message
    assert "status=403" in message
    assert "response_text=''" in message
    assert len(client.calls) == len(CONNECTOR_LIST_RETRY_DELAYS_SECONDS) + 1
    assert clock.sleeps == list(CONNECTOR_LIST_RETRY_DELAYS_SECONDS)
    assert exc_info.value.__cause__ is errors[-1]


@pytest.mark.parametrize(
    "error",
    [
        APIError("forbidden", status_code=403, response_text="missing connector scope"),
        APIError("server error", status_code=500, response_text="boom"),
        APIError("transport", status_code=None, response_text=""),
    ],
)
def test_non_transient_errors_raise_immediately(error):
    client = _ConnectorListClient([error])
    sleeps = []

    with pytest.raises(APIError) as exc_info:
        list_connectors(
            client,
            "wr-3",
            retry_delays=(0.25,),
            sleep=sleeps.append,
        )

    assert exc_info.value is error
    assert len(client.calls) == 1
    assert sleeps == []
