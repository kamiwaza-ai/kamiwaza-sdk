"""Live coverage for the seeding surfaces, reusing the shared live fixtures.

These exercise the generic seeding building blocks against a running platform
using the same authenticated client/auth helpers as the smoke tests
(``tests/integration/conftest.py``), so seeding and smoke draw from one
library. The environment-specific UAT profile (which models/extensions) lives
in the nightly seeding job, not here.
"""

from __future__ import annotations

import warnings
from uuid import uuid4

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.seeding import scoped_client_for_workroom

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _delete_and_confirm(admin_client, workroom_id) -> None:
    """Delete a throwaway workroom and confirm it is gone (ENG-10506).

    The client retries ``workroom_authority_unavailable`` internally, so a
    fenced delete resolves without help here. What this adds is the check
    that the workroom actually went away: a cleanup that silently no-ops
    leaves residue on the cluster, and the next run inherits it. Residue is
    warned about rather than raised — teardown must not manufacture a
    failure in a test whose assertions already passed.
    """
    try:
        admin_client.workrooms.admin_delete(workroom_id)
    except APIError as exc:
        warnings.warn(
            f"cleanup: could not delete workroom {workroom_id}: {exc}",
            stacklevel=1,
        )
        return

    try:
        admin_client.workrooms.get(workroom_id)
    except APIError:
        return  # gone, as intended
    warnings.warn(
        f"cleanup: workroom {workroom_id} still resolves after admin_delete",
        stacklevel=1,
    )


@pytest.fixture
def disposable_workroom(live_write_client, live_kamiwaza_client):
    """Create workrooms that are torn down best-effort after the test.

    Deleting a workroom can transiently 503 while the authority fence is held
    by another operation. Done inline in a ``finally:``, that turns a healthy
    assertion pass into a red test — the failure that shows up is
    ``admin_delete``, not the behavior under test. Cleanup problems belong in
    a warning; only the assertions decide pass/fail.
    """
    created = []

    def _create(**kwargs):
        workroom = live_write_client.workrooms.create(**kwargs)
        created.append(workroom)
        return workroom

    yield _create

    for workroom in reversed(created):
        _delete_and_confirm(live_kamiwaza_client, workroom.id)


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


def test_workroom_create_scopes_a_client_locally(
    live_write_client, disposable_workroom
):
    """Create a workroom, derive an explicit workroom-scoped client."""
    name = f"seed-probe-{uuid4().hex[:8]}"
    workroom = disposable_workroom(name=name, workroom_type="persistent")

    scoped = scoped_client_for_workroom(live_write_client, workroom.id)

    assert scoped is not live_write_client
    assert scoped._default_headers == {"X-Workroom-Id": str(workroom.id)}


def test_pat_workroom_enter_is_rejected(live_write_client, disposable_workroom):
    """PAT clients use explicit workroom scope, not selected-session enter."""
    name = f"seed-enter-pat-{uuid4().hex[:8]}"
    workroom = disposable_workroom(name=name, workroom_type="persistent")

    with pytest.raises(APIError) as exc_info:
        live_write_client.workrooms.enter(workroom.id)

    assert exc_info.value.status_code == 409
    assert _is_binding_invalid(exc_info.value)
