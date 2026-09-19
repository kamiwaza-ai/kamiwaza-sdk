"""Workroom administration and retirement on a live deployment (ENG-12325, T05).

Two evidence-bearing tests, one per capability, each owned by a disposable
non-admin user so that neither claim can rest on administrator authority:

* ``test_admin_finds_and_deletes_another_users_archived_workroom`` evidences
  ``workrooms.admin-oversight``. The user creates and archives a workroom and
  is refused both admin routes with the admin-role error; the administrator
  finds it in the platform-wide listing with that user as owner and the
  owner's username resolved, deletes it, and proves it left the listing but is
  retained as deleted. The capability covers the admin listing and admin
  delete and names no preview route, so nothing here defers to one.
* ``test_owner_archives_restores_previews_and_deletes_a_workroom`` evidences
  ``workrooms.archive-and-delete``. Every owner action is the non-admin's; only
  the final retention read uses the administrator listing. The owner archives
  the workroom, which hides it from the default listing and refuses an update,
  and restores it, after which the same update is accepted. The owner then
  seeds one catalog dataset through a session binding; the lifecycle summary
  and delete preview must report that dataset and the owner as the one member
  losing access. The owner deletes the dataset, then the workroom, and proves
  it gone and retained as deleted. Restore, the lifecycle summary, and the
  delete preview have no SDK method, so they are called over the raw API and
  are not SDK coverage. Not evidenced: the cascading purge of a deleted
  workroom's tagged resources. The test removes its own dataset first, so that
  nothing it created can outlive a failure while the workroom is archived.

The administrator is the shared admin client and never enters a workroom.
Opt-in: ``KAMIWAZA_TEST_DISPOSABLE_IDENTITIES=1``; see
``_workroom_disposable_user``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.workrooms import Workroom
from kamiwaza_sdk.services.context import ContextService

from . import _workroom_disposable_user as disposable
from ._workroom_support import (
    WorkroomLedger,
    admin_workroom_ids,
    find_admin_workroom,
    refusal,
    when_authority_projected,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    disposable.requires_disposable_identities,
]

_ADMIN_ROLE_REQUIRED = "Role 'admin' is required to access this resource"


def _listed_ids(workrooms: list[Workroom]) -> set[str]:
    return {str(workroom.id) for workroom in workrooms}


@pytest.fixture(scope="session", autouse=True)
def refuse_credential_echo_early(pytestconfig: pytest.Config) -> None:
    """Refuse before any live fixture authenticates.

    Session-scoped and autouse, so it runs ahead of the session fixtures that
    hold the shared administrator's credentials; it requests only the config,
    so it cannot pull them in itself.
    """
    disposable.refuse_credential_echo(pytestconfig)


@pytest.fixture
def workroom_user(
    request: pytest.FixtureRequest,
    live_kamiwaza_client: KamiwazaClient,
    live_server_available: str,
    client_factory: disposable.ClientFactory,
) -> Iterator[disposable.WorkroomUser]:
    yield from disposable.session_user(
        request, live_kamiwaza_client, client_factory, live_server_available
    )


@pytest.fixture
def ledger(
    live_kamiwaza_client: KamiwazaClient, workroom_user: disposable.WorkroomUser
) -> WorkroomLedger:
    """Removes what a failed test left, as part of the user's own teardown.

    Session-bound: the user writes a dataset by entering its workroom.
    Registered on the user rather than torn down here: pytest stops calling the
    remaining finalizers when one raises a ``BaseException``, so a Ctrl-C in a
    finalizer of this fixture would leave the account behind.
    """
    record = WorkroomLedger(
        admin=live_kamiwaza_client, owner=workroom_user.client, binds_session=True
    )
    workroom_user.cleanups.append(
        ("remove what the workroom ledger recorded", record.remove_remaining)
    )
    return record


def test_admin_finds_and_deletes_another_users_archived_workroom(
    live_kamiwaza_client: KamiwazaClient,
    workroom_user: disposable.WorkroomUser,
    ledger: WorkroomLedger,
) -> None:
    admin = live_kamiwaza_client
    user = workroom_user.client
    workroom_id = ledger.create_workroom("oversight")
    # The control for the absence check after the delete: the SDK answers a
    # listing payload without an "items" key with an empty list, so "not in the
    # listing" has to be read against a workroom the same query still finds.
    control_id = ledger.create_workroom("oversight-control")
    archived = when_authority_projected(lambda: user.workrooms.archive(workroom_id))
    assert archived.status == "archived"
    assert str(user.workrooms.get(workroom_id).id) == workroom_id

    with pytest.raises(APIError) as listing_denied:
        user.workrooms.admin_list(limit=1)
    assert refusal(listing_denied.value) == (403, _ADMIN_ROLE_REQUIRED)
    with pytest.raises(APIError) as delete_denied:
        user.workrooms.admin_delete(workroom_id)
    assert refusal(delete_denied.value) == (403, _ADMIN_ROLE_REQUIRED)

    found = find_admin_workroom(admin, workroom_id, include_deleted=False)
    assert found is not None, "the platform-wide listing omits a user's workroom"
    assert (found.status, found.owner_user_id) == ("archived", workroom_user.subject)
    # 1.2.1 resolves the owner for the admin view and answers None when it
    # cannot, so the username is what proves the identity was resolved.
    assert getattr(found, "owner_username", None) == workroom_user.username

    admin.workrooms.admin_delete(workroom_id)
    # One traversal, both sides: a second lookup for the control would be a
    # different response, and an "items"-less payload would answer the first
    # with an empty list.
    listed = admin_workroom_ids(admin, include_deleted=False)
    assert control_id in listed, "the default admin listing returned nothing at all"
    assert workroom_id not in listed
    retained = find_admin_workroom(admin, workroom_id, include_deleted=True)
    assert retained is not None, "include_deleted omits an admin-deleted workroom"
    assert retained.status == "deleted"
    with pytest.raises(NotFoundError):
        user.workrooms.get(workroom_id)
    ledger.forget_workroom(workroom_id)


def test_owner_archives_restores_previews_and_deletes_a_workroom(
    live_kamiwaza_client: KamiwazaClient,
    workroom_user: disposable.WorkroomUser,
    ledger: WorkroomLedger,
) -> None:
    owner = workroom_user.client
    workrooms = owner.workrooms
    workroom_id = ledger.create_workroom("retire")
    # Read out of the same response as the absence check below: the SDK answers
    # a listing payload without an "items" key with an empty list, which would
    # satisfy "not in the listing" on its own.
    control_id = ledger.create_workroom("retire-control")

    archived = when_authority_projected(lambda: workrooms.archive(workroom_id))
    assert archived.status == "archived"
    default_listing = _listed_ids(workrooms.list())
    assert control_id in default_listing, "the default listing returned nothing at all"
    assert workroom_id not in default_listing
    assert workroom_id in _listed_ids(workrooms.list(include_archived=True))
    # 1.2.1 answers a repeat archive with the archived record (observed
    # 2026-09-17); WorkroomService.archive's docstring still documents a 409.
    assert workrooms.archive(workroom_id).status == "archived", "re-archive refused"
    change = "sent while archived, then once restored"
    with pytest.raises(APIError) as read_only:
        workrooms.update(workroom_id, description=change)
    assert read_only.value.status_code == 409

    restored = owner.post(f"/workrooms/{workroom_id}/restore")
    assert (restored["id"], restored["status"]) == (workroom_id, "active")
    assert workroom_id in _listed_ids(workrooms.list())
    updated = workrooms.update(workroom_id, description=change)
    assert updated.description == change

    # Neither reports a catalog entry before one is seeded, so the 1 read after
    # seeding is the seeded dataset.
    before_summary = owner.get(f"/workrooms/{workroom_id}/lifecycle/summary")
    before_preview = owner.get(f"/workrooms/{workroom_id}/delete-preview")
    before = {item["type"]: item["count"] for item in before_preview["impact_items"]}
    assert (before_summary["catalog_entry_count"], before["catalog_entries"]) == (0, 0)

    # One dataset gives the summary and the preview a count known in advance.
    # It is seeded only now: an archived workroom refuses writes, so a failure
    # while archived would leave a dataset the ledger could not delete. Writes
    # need a session binding, so the owner enters, creates it, and leaves.
    entered_id = str(workrooms.enter(workroom_id).workroom_id)
    assert entered_id == workroom_id
    name, urn = ledger.create_dataset("retire-data", workroom_id)
    assert owner.catalog.datasets.get(urn).urn == urn
    left_id = str(workrooms.leave().workroom_id)
    assert left_id == ContextService.DEFAULT_WORKROOM_ID

    # Only counts this test controls are asserted: the seeded dataset and the
    # owner's membership. App deployment and extension counts depend on
    # provisioning the deployment does on its own schedule, and read 0 in one
    # run (2026-09-17), where no comparison could fail.
    summary = owner.get(f"/workrooms/{workroom_id}/lifecycle/summary")
    assert (summary["workroom_id"], summary["status"]) == (workroom_id, "active")
    assert (summary["catalog_entry_count"], summary["active_member_count"]) == (1, 1)

    preview = owner.get(f"/workrooms/{workroom_id}/delete-preview")
    assert preview["workroom_id"] == workroom_id
    losing = [(m["user_id"], m["role"]) for m in preview["members_losing_access"]]
    assert losing == [(workroom_user.subject, "owner")]
    impact = {item["type"]: item["count"] for item in preview["impact_items"]}
    assert (impact["members"], impact["catalog_entries"]) == (1, 1)

    # Asserted like every other enter here: the delete below depends on this
    # binding, so a re-enter that silently bound elsewhere would make the
    # deletion prove nothing about this workroom.
    rebound_id = str(workrooms.enter(workroom_id).workroom_id)
    assert rebound_id == workroom_id
    owner.catalog.datasets.delete(urn)
    ledger.proven_gone(lambda: owner.catalog.datasets.get(urn), name)
    left_again = str(workrooms.leave().workroom_id)
    assert left_again == ContextService.DEFAULT_WORKROOM_ID

    assert str(workrooms.get(workroom_id).id) == workroom_id
    workrooms.delete(workroom_id)
    with pytest.raises(NotFoundError):
        workrooms.get(workroom_id)
    with pytest.raises(NotFoundError):
        workrooms.delete(workroom_id)
    retained = find_admin_workroom(
        live_kamiwaza_client, workroom_id, include_deleted=True
    )
    assert retained is not None, "a deleted workroom's record was not retained"
    assert retained.status == "deleted"
    ledger.forget_workroom(workroom_id)
