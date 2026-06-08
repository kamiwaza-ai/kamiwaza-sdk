"""ENG-6504 regression guard: ``live_server_available`` must fail the session
loudly when the platform is unreachable, not silently skip every dependent
test.

The integration conftest's ``live_server_available`` previously called
``pytest.skip(...)`` on the three "infrastructure broken" paths (no response,
5xx, unexpected 4xx). Because pytest caches skips raised from session-scoped
fixtures and replays them on every dependent test, a single unreachable ``/ping``
turned into 165 silent skips and a green smoke report (see ENG-6504).

These tests pin the new contract: each of those three paths must call
``pytest.exit``, which raises ``_pytest.outcomes.Exit`` (a distinct exception
from ``SystemExit``) so the session terminates with a non-zero return code
instead of cascading skips.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from _pytest.outcomes import Exit

# Mark the whole module so the regression guard runs in the ``make test-unit``
# lane. Without this, ``pytest -m unit`` deselects all six tests and the guard
# silently never executes — ironic for a PR whose thesis is "don't silently
# skip" (PR #136 re-review Medium #1).
pytestmark = pytest.mark.unit

CONFTEST_PATH = (
    Path(__file__).resolve().parents[1] / "integration" / "conftest.py"
)


@pytest.fixture(scope="module")
def integration_conftest():
    spec = importlib.util.spec_from_file_location(
        "_integration_conftest_under_test", CONFTEST_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def live_server_fn(integration_conftest):
    """Underlying function of the ``live_server_available`` fixture."""
    fn = getattr(integration_conftest.live_server_available, "__wrapped__", None)
    assert fn is not None, "pytest fixture must expose __wrapped__ for testing"
    return fn


def _make_response(status_code: int) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    return response


def test_live_server_available_exits_when_unreachable(
    integration_conftest, live_server_fn, monkeypatch
) -> None:
    """No response after retries -> pytest.exit, not pytest.skip cascade.

    Also pins the retry contract: requests.get is invoked exactly 3 times before
    the fixture gives up. A regression that short-circuits the loop or drops a
    retry would still satisfy "raises Exit" but should still fail this test.
    """
    monkeypatch.setattr(integration_conftest.time, "sleep", lambda _: None)
    mock_get = Mock(side_effect=requests.ConnectionError("connection refused"))
    monkeypatch.setattr(integration_conftest.requests, "get", mock_get)

    with pytest.raises(Exit) as excinfo:
        live_server_fn("https://example.invalid/api")

    assert excinfo.value.returncode == 2
    assert mock_get.call_count == 3, (
        f"expected 3 retry attempts before exit, got {mock_get.call_count}"
    )


def test_live_server_available_exits_on_5xx(
    integration_conftest, live_server_fn, monkeypatch
) -> None:
    """5xx response -> pytest.exit, not pytest.skip cascade."""
    monkeypatch.setattr(integration_conftest.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        integration_conftest.requests,
        "get",
        Mock(return_value=_make_response(503)),
    )

    with pytest.raises(Exit) as excinfo:
        live_server_fn("https://example.invalid/api")

    assert excinfo.value.returncode == 2


def test_live_server_available_exits_on_unexpected_4xx(
    integration_conftest, live_server_fn, monkeypatch
) -> None:
    """4xx other than 401/403 -> pytest.exit (config / base URL is wrong)."""
    monkeypatch.setattr(integration_conftest.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        integration_conftest.requests,
        "get",
        Mock(return_value=_make_response(418)),
    )

    with pytest.raises(Exit) as excinfo:
        live_server_fn("https://example.invalid/api")

    assert excinfo.value.returncode == 2


def test_live_server_available_passes_on_200(
    integration_conftest, live_server_fn, monkeypatch
) -> None:
    """Healthy server -> returns base URL, no skip, no exit."""
    monkeypatch.setattr(
        integration_conftest.requests,
        "get",
        Mock(return_value=_make_response(200)),
    )

    result = live_server_fn("https://example.invalid/api")
    assert result == "https://example.invalid/api"


def test_live_server_available_passes_on_401(
    integration_conftest, live_server_fn, monkeypatch
) -> None:
    """401 is a legitimate auth-required response, not infra failure."""
    monkeypatch.setattr(
        integration_conftest.requests,
        "get",
        Mock(return_value=_make_response(401)),
    )

    result = live_server_fn("https://example.invalid/api")
    assert result == "https://example.invalid/api"


def test_live_server_available_passes_on_403(
    integration_conftest, live_server_fn, monkeypatch
) -> None:
    """403 is a legitimate forbidden response, not infra failure."""
    monkeypatch.setattr(
        integration_conftest.requests,
        "get",
        Mock(return_value=_make_response(403)),
    )

    result = live_server_fn("https://example.invalid/api")
    assert result == "https://example.invalid/api"
