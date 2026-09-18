"""Workroom export against a live deployment (ENG-12325, plan T04).

``test_export_enumerates_a_seeded_dataset_in_manifest_summary_and_bundle`` is
the evidence-bearing test for ``workrooms.export-bundle``. It seeds one catalog
dataset into a new workroom and requires each export surface to account for it:
the manifest names it as the only exportable dataset, the ingestion summary
counts one catalog entry, and the downloaded ZIP holds exactly the workroom
metadata, the manifest naming it, both indexes, and its descriptor file. The
export is synchronous on 1.2.1, so there is no job to poll; a response without
those entries fails.

The shared admin client runs this test and never enters a workroom: it writes
through the explicit ``X-Workroom-Id`` header. That relies on the client being
a PAT, which the live fixtures mint whenever credentials resolve; a password
session with no binding is refused such writes. The test does create, and then
delete, one workroom and one dataset owned by that admin, which other runs
authenticating as the same admin can see in the meantime.

Workrooms on the 1.2.1 evidence deployment can carry app deployments and
extensions the deployment provisions on its own schedule (present in some
reads and absent in others on 2026-09-17), which the manifest lists as
non-exportable. The assertions tolerate any number of them and pin the seeded
dataset.

Not evidenced here: the documented exclusion of underlying data from the
bundle. The seeded dataset is metadata pointing at an unpopulated location, so
there is no payload the archive could wrongly include.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from contextlib import ExitStack

import pytest
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import NotFoundError

from . import _workroom_disposable_user as disposable
from ._workroom_support import (
    WorkroomLedger,
    await_condition,
    dataset_urns,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


@pytest.fixture(scope="session", autouse=True)
def refuse_credential_echo_early(pytestconfig: pytest.Config) -> None:
    """This test carries the shared admin's bearer; refuse a run that would print it.

    Session-scoped and autouse, so in a run of these evidence modules it
    refuses before the session fixtures that hold the administrator's token
    authenticate. It cannot cover a session whose earlier tests already
    reached them.
    """
    disposable.refuse_credential_echo(pytestconfig)


@pytest.fixture
def ledger(live_kamiwaza_client: KamiwazaClient) -> Iterator[WorkroomLedger]:
    record = WorkroomLedger(
        admin=live_kamiwaza_client, owner=live_kamiwaza_client, binds_session=False
    )
    yield record
    record.remove_remaining()


@pytest.fixture
def scopes(ledger: WorkroomLedger) -> ExitStack:
    """Closes every workroom-scoped client a test opens, during the ledger's own
    teardown: a finalizer of its own would abort that teardown on a Ctrl-C."""
    stack = ExitStack()
    ledger.register_cleanup("close the workroom-scoped clients", stack.close)
    return stack


def test_export_enumerates_a_seeded_dataset_in_manifest_summary_and_bundle(
    live_kamiwaza_client: KamiwazaClient, ledger: WorkroomLedger, scopes: ExitStack
) -> None:
    workrooms = live_kamiwaza_client.workrooms
    workroom_id = ledger.create_workroom("export")
    scoped = scopes.enter_context(live_kamiwaza_client.workroom_scope(workroom_id))
    name, urn = ledger.create_dataset("export-data", workroom_id)
    await_condition(
        lambda: dataset_urns(scoped, name) == {urn}, "the seeded dataset does not list"
    )

    manifest = workrooms.get_export_manifest(workroom_id)
    assert str(manifest.workroom_id) == workroom_id
    assert any(item.type == "metadata" for item in manifest.items)
    datasets = [item for item in manifest.items if item.type == "dataset"]
    assert [(item.name, item.exportable) for item in datasets] == [(name, True)]

    summary = workrooms.get_ingestion_summary(workroom_id)
    assert str(summary.workroom_id) == workroom_id
    assert summary.catalog_entries == 1

    raw = workrooms.export_bundle(workroom_id)
    assert isinstance(raw, bytes)
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        names = set(bundle.namelist())
        assert json.loads(bundle.read("workroom.json"))["id"] == workroom_id
        bundled_manifest = json.loads(bundle.read("manifest.json"))
        assert bundled_manifest["workroom_id"] == workroom_id
        bundled_datasets = [
            (item["name"], item["exportable"])
            for item in bundled_manifest["items"]
            if item["type"] == "dataset"
        ]
        assert bundled_datasets == [(name, True)]
        index = json.loads(bundle.read("datasets/index.json"))
        assert [entry["urn"] for entry in index] == [urn]
        descriptor_path = f"datasets/{index[0]['id']}.json"
        # Metadata and descriptors only: the archive holds nothing else.
        assert names == {
            "workroom.json",
            "manifest.json",
            "data_sources/index.json",
            "datasets/index.json",
            descriptor_path,
        }
        assert json.loads(bundle.read("data_sources/index.json")) == []
        assert json.loads(bundle.read(descriptor_path)) == index[0]

    assert scoped.catalog.datasets.get(urn).urn == urn
    scoped.catalog.datasets.delete(urn)
    await_condition(
        lambda: dataset_urns(scoped, name) == set(), "a deleted dataset still lists"
    )
    after_delete = workrooms.get_ingestion_summary(workroom_id)
    # The SDK defaults an omitted count to 0, so the field must be in the response.
    assert "catalog_entries" in after_delete.model_fields_set
    assert after_delete.catalog_entries == 0
    ledger.proven_gone(lambda: scoped.catalog.datasets.get(urn), name)
    assert str(workrooms.get(workroom_id).id) == workroom_id
    workrooms.delete(workroom_id)
    with pytest.raises(NotFoundError):
        workrooms.get(workroom_id)
    ledger.forget_workroom(workroom_id)
