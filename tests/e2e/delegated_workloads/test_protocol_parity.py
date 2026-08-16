"""Published vectors have identical Python SDK and raw HTTP semantics."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from .protocol_parity_support import (
    CLIENT_FACTORIES,
    RecordingSession,
    error_response,
    expected_error,
    expected_exchange,
    response_for_exchange,
)
from .raw_http_conformance_client import RawHTTPNeutralClient

pytestmark = pytest.mark.e2e
_ROOT = Path(__file__).parents[3]
_FIXTURE_PATH = _ROOT / "docs/delegated-workloads/conformance-v1.json"


def _fixture() -> dict[str, Any]:
    value = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("client_factory", CLIENT_FACTORIES)
@pytest.mark.parametrize("exchange", _fixture()["http_exchanges"])
def test_python_sdk_and_raw_http_run_the_same_success_vectors(
    client_factory,
    exchange: dict[str, Any],
) -> None:
    session = RecordingSession(response_for_exchange(exchange))

    observation = client_factory(session).execute(exchange)

    assert observation == expected_exchange(exchange)
    call = session.calls[0]
    request = exchange["request"]
    assert (call.method, call.url.split(_fixture()["protocol"]["base_path"])[-1]) == (
        request["method"],
        request["path"],
    )
    assert json.loads(call.body) == request["body"]
    expected_headers = {name.casefold() for name in request["headers"]}
    assert expected_headers <= {name.casefold() for name in call.headers}


@pytest.mark.parametrize("client_factory", CLIENT_FACTORIES)
@pytest.mark.parametrize("mapping", _fixture()["error_mapping"])
def test_python_sdk_and_raw_http_run_the_same_error_vectors(
    client_factory,
    mapping: dict[str, Any],
) -> None:
    session = RecordingSession(error_response(mapping))

    observation = client_factory(session).error(mapping)

    assert observation == expected_error(mapping)


def test_raw_http_parity_client_has_no_python_sdk_dependency() -> None:
    source = Path(RawHTTPNeutralClient.__module__.replace(".", "/") + ".py")
    path = _ROOT / "tests" / source
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(name.startswith("kamiwaza_sdk") for name in imports)
