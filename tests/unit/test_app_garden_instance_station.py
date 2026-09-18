"""Unit cover for the App Garden evidence module's instance station.

ENG-12432. The station exists because a deployment status is not proof that
anything runs: ``AppInstance.status`` defaults to ``UNINITIALIZED``, and a
deployment can report a running status while every instance row it owns is dead.

The platform keeps the two independent and checks both itself before treating a
deployment as usable -- ``dep.status == "DEPLOYED" and any(inst.status ==
"DEPLOYED" for inst in dep.instances)`` in its own readiness filter -- so
asserting instance status here is not bounded by the poll the test already did.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.integration import test_app_garden_lifecycle_live as garden

NAME = "eng12432-garden-fake"


def _client(*instance_statuses: str, deployment_status: str = "DEPLOYED"):
    """A deployment reporting ``deployment_status`` over the given instances."""
    deployment_id = uuid4()
    row = SimpleNamespace(id=deployment_id, name=NAME)
    instances = [
        SimpleNamespace(id=uuid4(), deployment_id=deployment_id, status=status)
        for status in instance_statuses
    ]
    client = SimpleNamespace(
        apps=SimpleNamespace(
            get_deployment_status=lambda _id: deployment_status,
            get_deployment=lambda _id: row,
            list_deployments=lambda: [row],
            list_instances=lambda _id: instances,
        )
    )
    return client, deployment_id


def test_a_running_instance_satisfies_the_station() -> None:
    client, deployment_id = _client("DEPLOYED")
    garden._assert_monitoring_stations(client, deployment_id, NAME)


def test_instance_rows_that_are_all_dead_do_not_satisfy_the_station() -> None:
    """The case the station exists for: correctly correlated, nothing running."""
    client, deployment_id = _client("FAILED", "STOPPED")
    with pytest.raises(AssertionError, match="none of its 2 instance"):
        garden._assert_monitoring_stations(client, deployment_id, NAME)


def test_an_uninitialised_instance_does_not_satisfy_the_station() -> None:
    """AppInstance.status defaults to this, so a bare row must not count."""
    client, deployment_id = _client("UNINITIALIZED")
    with pytest.raises(AssertionError, match="none of its 1 instance"):
        garden._assert_monitoring_stations(client, deployment_id, NAME)


def test_no_instances_at_all_does_not_satisfy_the_station() -> None:
    client, deployment_id = _client()
    with pytest.raises(AssertionError, match="lists no instances"):
        garden._assert_monitoring_stations(client, deployment_id, NAME)


def test_an_instance_belonging_to_another_deployment_is_refused() -> None:
    """A filter the platform ignored must not let a stranger satisfy the claim."""
    client, deployment_id = _client("DEPLOYED")
    client.apps.list_instances = lambda _id: [
        SimpleNamespace(id=uuid4(), deployment_id=uuid4(), status="DEPLOYED")
    ]
    with pytest.raises(AssertionError, match="belonging to other"):
        garden._assert_monitoring_stations(client, deployment_id, NAME)
