"""In-memory doubles for the ENG-12325 workroom ledger tests.

One deployment-free stand-in for each collaborator the ledger drives: the
workroom and dataset endpoints, the owner client and the scope it opens, the
administrator, and the admin listing. ``make_ledger`` wires them together so a
test states only the behaviour it is pinning.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

from kamiwaza_sdk.exceptions import APIError, NotFoundError
from tests.integration import _workroom_support as support


class Workrooms:
    """Owner-side workroom calls, with a server-side store."""

    def __init__(
        self,
        *,
        fail_create_after_storing: bool = False,
        create_error: APIError | None = None,
    ) -> None:
        self.rooms: dict[str, str] = {}
        self.deleted: set[str] = set()
        self.archived: set[str] = set()
        self.calls: list[tuple[str, str | None]] = []
        self.fail_create_after_storing = fail_create_after_storing
        self.create_error = create_error
        # The session's own binding: a bound client reads in that workroom.
        self.bound: str | None = None

    def create(self, name, _workroom_type):
        if self.create_error is not None:
            # As a server does: the name is never stored.
            raise self.create_error
        workroom_id = str(uuid4())
        self.rooms[workroom_id] = name
        if self.fail_create_after_storing:
            raise ValueError("response validation failed after the create")
        return SimpleNamespace(id=workroom_id)

    def list(self, *, include_archived=False):
        return [
            SimpleNamespace(id=workroom_id, name=name)
            for workroom_id, name in self.rooms.items()
            if workroom_id not in self.deleted
            and (include_archived or workroom_id not in self.archived)
        ]

    def get(self, workroom_id):
        if workroom_id in self.deleted or workroom_id not in self.rooms:
            raise NotFoundError("gone")
        return SimpleNamespace(id=workroom_id)

    def enter(self, workroom_id):
        # Refuses a workroom that is gone, like ``get`` above and like the
        # server, which answers 404 for entering a deleted workroom. A double
        # whose enter always succeeds cannot reach the teardown path where a
        # test deletes its workroom while a dataset record is still
        # outstanding -- which is how that path shipped unnoticed.
        self.calls.append(("enter", workroom_id))
        if workroom_id in self.deleted or workroom_id not in self.rooms:
            raise NotFoundError(f"Workroom {workroom_id} not found")
        self.bound = workroom_id

    def leave(self):
        self.calls.append(("leave", None))
        self.bound = None


@dataclass(frozen=True)
class CatalogState:
    """What every view of one owner's catalog shares.

    The stored datasets, the call log, and the faults a test sets are one
    owner's server state; only the scope a view reads through differs between
    them. Passing them as one value keeps each view's constructor narrow.
    """

    store: dict[str, str]
    calls: list
    faults: SimpleNamespace


class Datasets:
    """The catalog dataset client: POST-only create, by-URN get and delete.

    ``faults`` is shared by every view of one owner (bound or header-scoped), so
    a test can make the server misbehave however the ledger reaches it.
    """

    def __init__(
        self,
        catalog: CatalogState,
        scope: str | None,
        bound: Callable[[], str | None] | None = None,
    ) -> None:
        self.store = catalog.store
        self.calls = catalog.calls
        self.faults = catalog.faults
        self.scope = scope
        # An unscoped client still reads in whatever the session entered.
        self.bound = bound or (lambda: None)

    def create(self, payload):
        self.calls.append(("create", self.scope))
        urn = self.faults.returned_urn or support.expected_dataset_urn(payload.name)
        self.store[urn] = payload.name
        if self.faults.create_error is not None:
            raise self.faults.create_error
        return urn

    def get(self, urn):
        self.calls.append(("get", self.scope))
        if urn not in self.store or self._hidden_here(urn):
            raise NotFoundError("Dataset not found")
        return SimpleNamespace(urn=urn)

    def _hidden_here(self, urn):
        """A mis-scoped dataset: readable unscoped, absent from any workroom."""
        scope = self.scope if self.scope is not None else self.bound()
        return scope is not None and urn in self.faults.mis_scoped

    def delete(self, urn):
        self.calls.append(("delete", self.scope))
        if urn not in self.store or self._hidden_here(urn):
            raise NotFoundError("Dataset not found")
        if self.faults.delete_applies:
            del self.store[urn]


class Owner:
    def __init__(self, workrooms: Workrooms) -> None:
        self.workrooms = workrooms
        self.store: dict[str, str] = {}
        self.dataset_faults = SimpleNamespace(
            returned_urn=None,
            create_error=None,
            delete_applies=True,
            mis_scoped=frozenset(),
        )
        self.opened_scopes = 0
        self.closed_scopes = 0
        self.catalog = SimpleNamespace(
            datasets=Datasets(
                CatalogState(self.store, workrooms.calls, self.dataset_faults),
                None,
                bound=lambda: workrooms.bound,
            )
        )

    def workroom_scope(self, workroom_id):
        self.opened_scopes += 1
        return ScopedClient(self, workroom_id)


class ScopedClient:
    """A header-scoped client: a context manager that counts its closes."""

    def __init__(self, owner: Owner, workroom_id: str) -> None:
        self.owner = owner
        self.catalog = SimpleNamespace(
            datasets=Datasets(
                CatalogState(owner.store, owner.workrooms.calls, owner.dataset_faults),
                workroom_id,
            )
        )

    def __enter__(self) -> ScopedClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.owner.closed_scopes += 1


class Admin:
    def __init__(self, workrooms: Workrooms) -> None:
        self.workrooms = SimpleNamespace(admin_delete=self._admin_delete)
        self._owner_rooms = workrooms
        self.admin_deleted: list[str] = []

    def _admin_delete(self, workroom_id):
        self.admin_deleted.append(workroom_id)
        self._owner_rooms.deleted.add(workroom_id)


def make_ledger(
    *,
    binds_session: bool,
    fail_create: bool = False,
    create_error: APIError | None = None,
):
    workrooms = Workrooms(
        fail_create_after_storing=fail_create, create_error=create_error
    )
    owner = Owner(workrooms)
    admin = Admin(workrooms)
    ledger = support.WorkroomLedger(
        admin=admin, owner=owner, binds_session=binds_session
    )
    return ledger, workrooms, owner, admin


def rooms(*ids: str) -> list:
    return [SimpleNamespace(id=workroom_id) for workroom_id in ids]
