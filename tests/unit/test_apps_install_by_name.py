from __future__ import annotations

import uuid
import warnings

import pytest

from kamiwaza_sdk.exceptions import NotFoundError
from kamiwaza_sdk.services.apps import AppService

pytestmark = pytest.mark.unit

_TS = "2026-01-01T00:00:00Z"


def _template(name: str, version: str = "1.0.0") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "version": version,
        "source_type": "kamiwaza",
        "visibility": "private",
        "compose_yml": "services: {}",
        "risk_tier": 1,
        "created_at": _TS,
    }


def _deployment(name: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "requested_at": _TS,
        "created_at": _TS,
        "status": "UNINITIALIZED",
    }


class DummyClient:
    """Records calls; serves template-list pages in sequence."""

    def __init__(self, template_pages, deploy_response):
        self._template_pages = list(template_pages)
        self._deploy_response = deploy_response
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, path: str, **kwargs):
        self.calls.append(("GET", path, kwargs))
        if path == "/apps/app_templates":
            return self._template_pages.pop(0)
        raise AssertionError(f"Unexpected GET {path}")

    def post(self, path: str, **kwargs):
        self.calls.append(("POST", path, kwargs))
        if path == "/apps/deploy_app":
            return self._deploy_response
        if path == "/apps/garden/import":
            return {"imported_count": 1}
        raise AssertionError(f"Unexpected POST {path}")


def _service(client) -> AppService:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return AppService(client)


def test_install_by_name_resolves_template_and_deploys_with_workroom_header():
    template = _template("kaizen", "2.0.2")
    client = DummyClient([[template]], _deployment("kaizen"))
    service = _service(client)

    deployment = service.install_by_name("kaizen", version="2.0.2", workroom_id="wr-1")

    assert deployment.name == "kaizen"
    post_calls = [c for c in client.calls if c[0] == "POST"]
    assert len(post_calls) == 1
    _, path, kwargs = post_calls[0]
    assert path == "/apps/deploy_app"
    assert str(kwargs["json"]["template_id"]) == template["id"]
    assert kwargs["headers"] == {"X-Workroom-Id": "wr-1"}


def test_install_by_name_syncs_catalog_when_template_missing():
    template = _template("skills-library")
    # First lookup empty, then (after import) the template appears.
    client = DummyClient([[], [template]], _deployment("skills-library"))
    service = _service(client)

    service.install_by_name("skills-library")

    paths = [(c[0], c[1]) for c in client.calls]
    assert paths == [
        ("GET", "/apps/app_templates"),
        ("POST", "/apps/garden/import"),
        ("GET", "/apps/app_templates"),
        ("POST", "/apps/deploy_app"),
    ]


def test_install_by_name_raises_when_not_found_and_no_sync():
    client = DummyClient([[]], _deployment("nope"))
    service = _service(client)

    with pytest.raises(NotFoundError, match="No catalog template named 'nope'"):
        service.install_by_name("nope", sync_if_missing=False)

    # No deploy attempted.
    assert not any(c[1] == "/apps/deploy_app" for c in client.calls)
