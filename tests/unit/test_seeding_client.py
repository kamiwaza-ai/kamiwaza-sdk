from __future__ import annotations

from unittest.mock import patch

import pytest

from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.seeding.client import (
    build_client_from_env,
    scoped_client_for_workroom,
)

pytestmark = pytest.mark.unit

_ENTER = "kamiwaza_sdk.services.workrooms.WorkroomService.enter"
_WID = "725aba63-4b21-4346-8e83-7dfffe526740"


def _platform_client() -> KamiwazaClient:
    client = KamiwazaClient(base_url="https://example.test/api", api_key="global-pat")
    client.session.verify = False
    return client


def test_scoped_client_scopes_header_and_binds_session():
    client = _platform_client()

    with patch(_ENTER) as mock_enter:
        scoped = scoped_client_for_workroom(client, _WID)

    assert scoped is not client
    assert scoped.base_url == "https://example.test/api"
    assert scoped.authenticator is client.authenticator
    assert scoped._default_headers == {"X-Workroom-Id": _WID}
    # TLS setting carries over from the parent.
    assert scoped.session.verify is False
    # The server-side selected-workroom binding is established so per-workroom
    # writes carry authenticated workroom context.
    mock_enter.assert_called_once_with(_WID)


def test_enter_is_issued_without_the_workroom_scope_header():
    # Even when the caller passes an already-scoped client, enter must be issued
    # without the X-Workroom-Id header: a scoped POST hits the strict write-gate
    # and is rejected before the binding it would create exists.
    scoped_parent = _platform_client().workroom_scope(_WID)
    assert scoped_parent._default_headers == {"X-Workroom-Id": _WID}

    with patch(_ENTER, autospec=True) as mock_enter:
        scoped_client_for_workroom(scoped_parent, _WID)

    enter_client = mock_enter.call_args.args[0].client
    assert "X-Workroom-Id" not in enter_client._default_headers


@pytest.mark.parametrize(
    "response_data",
    [
        {"detail": {"error": {"class": "binding_invalid"}}},
        {"detail": "workroom_binding_invalid"},
    ],
)
def test_scoped_client_tolerates_pat_binding_rejection(response_data):
    # PAT / API-key credentials can't session-bind; the platform rejects enter
    # with 409 binding_invalid, which is tolerated so the header-scoped client
    # is returned unchanged — PATs authorize via the explicit header.
    client = _platform_client()
    rejection = APIError("conflict", status_code=409, response_data=response_data)

    with patch(_ENTER, side_effect=rejection) as mock_enter:
        scoped = scoped_client_for_workroom(client, _WID)

    assert scoped._default_headers == {"X-Workroom-Id": _WID}
    mock_enter.assert_called_once_with(_WID)


def test_scoped_client_propagates_enter_failure():
    # A workroom that can't be entered (e.g. never created) must fail loudly,
    # not silently return an unbound client that would 403 on the first write.
    client = _platform_client()

    with patch(_ENTER, side_effect=NotFoundError(f"Workroom {_WID} not found")):
        with pytest.raises(NotFoundError):
            scoped_client_for_workroom(client, _WID)


def test_scoped_client_propagates_non_binding_conflict():
    # A 409 that is NOT the binding-unsupported signal is a real conflict and
    # must propagate rather than being swallowed by the PAT-tolerance path.
    client = _platform_client()
    other = APIError(
        "conflict", status_code=409, response_data={"detail": "something_else"}
    )

    with patch(_ENTER, side_effect=other):
        with pytest.raises(APIError):
            scoped_client_for_workroom(client, _WID)


def test_build_client_from_env_requires_base_url(monkeypatch):
    monkeypatch.delenv("KAMIWAZA_BASE_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_BASE_URI", raising=False)

    with pytest.raises(SystemExit):
        build_client_from_env()


def test_build_client_from_env_uses_explicit_args():
    client = build_client_from_env(base_url="https://example.test/api", api_key="k")
    assert client.base_url == "https://example.test/api"
    assert client.authenticator.api_key == "k"
