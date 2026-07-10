"""Live coverage for the seeding surfaces, reusing the shared live fixtures.

These exercise the generic seeding building blocks against a running platform
using the same authenticated client/auth helpers as the smoke tests
(``tests/integration/conftest.py``), so seeding and smoke draw from one
library. The environment-specific UAT profile (which models/extensions) lives
in the nightly seeding job, not here.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.seeding import scoped_client_for_workroom

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def test_find_template_resolves_against_live_catalog(live_write_client):
    """install_by_name's resolver must query the live catalog without error.

    A name we never publish resolves to None rather than raising — proving the
    lookup path is sound regardless of catalog contents.
    """
    assert live_write_client.apps.find_template("seed-probe-does-not-exist") is None


def _is_binding_invalid(error: APIError) -> bool:
    payload = getattr(error, "response_data", None)
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        structured = detail.get("error")
        if isinstance(structured, dict):
            return structured.get("class") == "binding_invalid"
    return detail == "workroom_binding_invalid"


def test_workroom_create_scopes_a_client_locally(live_write_client, live_kamiwaza_client):
    """Create a workroom, derive an explicit workroom-scoped client, then clean up."""
    name = f"seed-probe-{uuid4().hex[:8]}"
    workroom = live_write_client.workrooms.create(name=name, workroom_type="persistent")
    try:
        scoped = scoped_client_for_workroom(live_write_client, workroom.id)

        assert scoped is not live_write_client
        assert scoped._default_headers == {"X-Workroom-Id": str(workroom.id)}
    finally:
        live_kamiwaza_client.workrooms.admin_delete(workroom.id)


def test_pat_workroom_enter_is_rejected(live_write_client, live_kamiwaza_client):
    """PAT clients use explicit workroom scope, not selected-session enter."""
    name = f"seed-enter-pat-{uuid4().hex[:8]}"
    workroom = live_write_client.workrooms.create(name=name, workroom_type="persistent")
    try:
        with pytest.raises(APIError) as exc_info:
            live_write_client.workrooms.enter(workroom.id)

        assert exc_info.value.status_code == 409
        assert _is_binding_invalid(exc_info.value)
    finally:
        live_kamiwaza_client.workrooms.admin_delete(workroom.id)
