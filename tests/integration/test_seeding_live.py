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
from tests.integration.test_context_live import _is_workroom_binding_unavailable

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def test_find_template_resolves_against_live_catalog(live_write_client):
    """install_by_name's resolver must query the live catalog without error.

    A name we never publish resolves to None rather than raising — proving the
    lookup path is sound regardless of catalog contents.
    """
    assert live_write_client.apps.find_template("seed-probe-does-not-exist") is None


def test_workroom_create_enter_scopes_a_client(live_write_client, live_kamiwaza_client):
    """Create a workroom, mint a workroom-scoped client via enter, then clean up."""
    name = f"seed-probe-{uuid4().hex[:8]}"
    workroom = live_write_client.workrooms.create(name=name, workroom_type="persistent")
    try:
        try:
            scoped = scoped_client_for_workroom(live_write_client, workroom.id)
        except APIError as exc:
            if _is_workroom_binding_unavailable(exc):
                pytest.skip(
                    "Workrooms enter binding is unavailable; skipping scoped seeding client live test"
                )
            raise
        # Either a reminted-token client or a graceful fall back to the parent.
        assert scoped is not None
    finally:
        live_kamiwaza_client.workrooms.admin_delete(workroom.id)
