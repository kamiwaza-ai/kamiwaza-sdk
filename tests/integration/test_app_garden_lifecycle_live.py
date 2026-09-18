"""Evidence for ``apps.app-garden`` — the deploy lifecycle beneath the catalog.

ENG-12432. The capability document's planned SDK arm is one run: resolve a
template, deploy, poll status, and stop, together with a direct assertion of
the reserved-environment-key boundary.

Both halves live in ONE test, because the map entry that names this capability
must not be satisfiable by the catalog half alone: a run that lists templates
and stops there is exactly the "catalog-only pass" the ticket rules out.

Two notes a maintainer of this test needs:

* A reserved key is present in the deployment read-back carrying a
  platform-supplied value, so the assertion is a **value mismatch**, not an
  absence. An absence assertion would fail against correct behaviour.
* ``UNRESERVED_LOOKALIKE_KEY`` is the passthrough control and is chosen
  deliberately; swapping it for an arbitrary name weakens what the test
  establishes.

The rationale for the key choices, and the boundary the arm is allowed to
claim, live with the ``apps.app-garden`` capability document in capability-kit
(internal). Keep this module's comments to test mechanics.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import suppress
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.apps import AppTemplate

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

# Reserved, and not redacted in responses — so the read-back value is
# comparable, which is what this probe needs.
RESERVED_PROBE_KEY = "KAMIWAZA_API_URL"

# The passthrough control. Chosen deliberately — see the capability document.
UNRESERVED_LOOKALIKE_KEY = "KAMIWAZA_MODEL_DEPLOYMENT_ID"

# Plainly outside any platform namespace.
PLAIN_PROBE_KEY = "ENG12432_PROBE"

# The statuses the platform treats as settled for a deployment.
# Every state the poll may stop on. RUNNING belongs here as well as in
# RUNNING_STATUSES: a status the arm treats as success must also end the wait,
# or a legitimately RUNNING deployment polls to the timeout and fails.
SETTLED_STATUSES = frozenset(
    {"DEPLOYED", "RUNNING", "FAILED", "STOP_REQUESTED", "STOPPED"}
)
RUNNING_STATUSES = frozenset({"DEPLOYED", "RUNNING"})
# What a successful stop must reach. STOP_REQUESTED is deliberately NOT accepted:
# it records that the request was received, not that the workload stopped, so a
# platform that acknowledges a stop and never performs it would satisfy the
# station while the workload kept running on a shared host. The reaping race the
# earlier spelling reached for is handled where it belongs, by checking the row's
# absence in _assert_retirement_station. test_tool_shed_lifecycle_live takes the
# same line for the same reason.
STOPPED_STATUSES = frozenset({"STOPPED"})

# Images served from a developer's local registry cannot be pulled by the
# cluster, so a template referencing one would fail the deploy for reasons that
# say nothing about this capability.
LOCAL_DEV_REGISTRY_MARKERS = ("host.docker.internal", "localhost:", "127.0.0.1:")


def _skip_or_fail(reason: str) -> NoReturn:
    if os.environ.get("KZ_REQUIRE_APP_GARDEN_EVIDENCE") == "1":
        pytest.fail(reason)
    pytest.skip(reason)
    raise AssertionError("unreachable: pytest.skip always raises")


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _deployable_template(client) -> AppTemplate:
    """Resolve a template the cluster can actually pull images for.

    ``KZ_APP_GARDEN_TEMPLATE`` pins a name explicitly. Otherwise prefer a
    template whose images are already pulled and none of which come from a
    developer's local registry — picking one that needs a multi-gigabyte pull
    would turn a capability test into an infrastructure endurance test.
    """
    templates = client.apps.list_templates()
    if not templates:
        _skip_or_fail("no app templates on this host; nothing to deploy")

    pinned = os.environ.get("KZ_APP_GARDEN_TEMPLATE")
    if pinned:
        for template in templates:
            if template.name == pinned:
                return template
        _skip_or_fail(f"KZ_APP_GARDEN_TEMPLATE={pinned!r} is not on this host")

    rejected: list[str] = []
    for template in templates:
        status = client.apps.check_image_status(template.id)
        images = list(getattr(status, "images", None) or [])
        if any(
            marker in image for image in images for marker in LOCAL_DEV_REGISTRY_MARKERS
        ):
            rejected.append(f"{template.name} (local dev registry)")
            continue
        if not getattr(status, "all_images_pulled", False):
            rejected.append(f"{template.name} (images not pulled)")
            continue
        return template

    _skip_or_fail(
        "no template has cluster-pullable images already present: "
        + "; ".join(rejected)
    )


def _is_listed(client, name: str) -> bool:
    """Whether the platform still carries a deployment row under this name.

    One predicate with two callers that read it in opposite directions: the
    retirement station treats absence as a completed stop, and the teardown
    finalizer treats presence as a leak. Spelling "still listed" out twice would
    leave the two free to drift apart.
    """
    return any(d.name == name for d in client.apps.list_deployments())


def _purge(client, deployment_id: UUID) -> None:
    """Remove the deployment row, not merely stop it.

    ``stop_deployment`` leaves a ``STOPPED`` row behind. A test that only
    stopped would accumulate residue on shared infrastructure every run.
    """
    with suppress(APIError):
        client.delete(f"/apps/deployment/{deployment_id}/purge")


@pytest.fixture
def retired_app_deployments(live_kamiwaza_client) -> Iterator[list[str]]:
    """Retire every deployment this test names, and prove each one is gone.

    A fixture finalizer rather than a ``finally`` block, for two reasons the
    inline form cannot cover on a shared host:

    * **Reconciliation by name.** The test registers its run-unique name
      *before* deploying, so a deploy whose server side committed but whose
      response then raised is still found and removed. An id captured after the
      call is unreachable on that path.
    * **Teardown failure stays visible.** pytest reports a finalizer error as
      its own ERROR, so a failed retirement neither masks the test's failure nor
      is masked by it. A `finally` block that raises would replace the original
      exception, and one that suppresses would hide the leak.
    """
    client = live_kamiwaza_client
    names: list[str] = []
    yield names

    leaked: list[str] = []
    for name in names:
        for deployment in client.apps.list_deployments():
            if deployment.name != name:
                continue
            with suppress(APIError):
                client.apps.stop_deployment(deployment.id)
            _purge(client, deployment.id)
        # Poll rather than asserting one call after the purge: a purge the
        # platform completes asynchronously would otherwise be reported as a
        # leak on a perfectly healthy run. The extensions finalizer already
        # polls; this keeps the three consistent.
        for _ in range(30):
            if not _is_listed(client, name):
                break
            time.sleep(1)
        else:
            leaked.append(name)
    assert not leaked, (
        f"deployments survived stop + purge and are leaked on a shared host: {leaked}"
    )


def test_app_garden_catalog_and_image_readiness(live_kamiwaza_client) -> None:
    """Catalog and image-readiness stations, read-only.

    Deliberately NOT mapped to the capability: a catalog listing establishes
    nothing about deploying, and the ticket is explicit that no catalog-only
    pass counts.
    """
    client = live_kamiwaza_client

    garden = client.apps.list_garden_apps()
    assert isinstance(garden, list)
    assert garden, "the remote garden catalog returned no apps"
    assert all(app.name and app.version for app in garden)

    templates = client.apps.list_templates()
    assert templates, "no local app templates to report image status for"

    status = client.apps.check_image_status(templates[0].id)
    assert str(status.template_id) == str(templates[0].id)
    assert isinstance(getattr(status, "all_images_pulled", None), bool), (
        "check_image_status did not report a boolean all_images_pulled"
    )


@pytest.mark.usefixtures("live_server_available")
def test_app_garden_deploy_lifecycle_and_reserved_env_keys(
    live_kamiwaza_client, retired_app_deployments
) -> None:
    """The documented arm: resolve, deploy, assert the key boundary, poll, retire."""
    client = live_kamiwaza_client
    template = _deployable_template(client)

    pre_existing = {str(d.id) for d in client.apps.list_deployments()}
    sentinel = uuid4().hex
    name = _unique("eng12432-garden")

    # Registered before the call that creates it: a deploy whose server side
    # committed and whose response then raised is still reconciled by name.
    retired_app_deployments.append(name)

    deployment = client.apps.deploy(
        template_id=template.id,
        name=name,
        env_vars={
            RESERVED_PROBE_KEY: f"reserved-{sentinel}",
            UNRESERVED_LOOKALIKE_KEY: f"lookalike-{sentinel}",
            PLAIN_PROBE_KEY: f"plain-{sentinel}",
        },
        min_copies=1,
        starting_copies=1,
    )
    deployment_id = deployment.id

    # A pre-existing row must never be mistaken for a successful deploy.
    assert str(deployment_id) not in pre_existing, (
        "deploy returned a deployment that already existed before the call"
    )

    _assert_reserved_key_boundary(client, deployment_id, sentinel)
    _assert_monitoring_stations(client, deployment_id, name)
    _assert_retirement_station(client, deployment_id, name)


def _assert_reserved_key_boundary(client, deployment_id: UUID, sentinel: str) -> None:
    """The caller's value is ignored for one key and preserved for the others."""
    stored = client.apps.get_deployment(deployment_id)
    env = stored.env_vars or {}
    assert env, "the deployment read-back exposed no env_vars to assert against"

    assert RESERVED_PROBE_KEY in env, (
        f"{RESERVED_PROBE_KEY} should be present in the read-back with a "
        f"platform-supplied value; got keys {sorted(env)[:12]}"
    )
    supplied = env[RESERVED_PROBE_KEY]
    assert supplied != f"reserved-{sentinel}", (
        f"the caller-supplied value for {RESERVED_PROBE_KEY} survived into "
        "the deployment environment"
    )
    # Differing from the sentinel is not enough: None or "" would also differ,
    # and the claim is that the platform supplies its OWN value for the key.
    assert isinstance(supplied, str) and supplied.strip(), (
        f"{RESERVED_PROBE_KEY} is present but carries no usable "
        f"platform-supplied value ({supplied!r}); the caller's value being "
        "discarded is only half the guarantee"
    )

    assert env.get(UNRESERVED_LOOKALIKE_KEY) == f"lookalike-{sentinel}", (
        f"{UNRESERVED_LOOKALIKE_KEY} must pass through untouched; got "
        f"{env.get(UNRESERVED_LOOKALIKE_KEY)!r}"
    )
    assert env.get(PLAIN_PROBE_KEY) == f"plain-{sentinel}", (
        f"{PLAIN_PROBE_KEY} must pass through; got {env.get(PLAIN_PROBE_KEY)!r}"
    )


def _assert_monitoring_stations(client, deployment_id: UUID, name: str) -> None:
    """Status is observable and the deployment is reachable by its own id."""
    status = _wait_for_settled_status(client, deployment_id)
    # SETTLED_STATUSES deliberately includes the terminal non-running states so
    # the poll stops; the deploy station is only satisfied by a running one.
    # Excluding FAILED alone would let a deployment that settled STOPPED or
    # STOP_REQUESTED pass as a successful deploy.
    assert status in RUNNING_STATUSES, (
        f"deployment {name} settled in {status!r}; the deploy station is "
        f"satisfied only by {sorted(RUNNING_STATUSES)}"
    )

    fetched = client.apps.get_deployment(deployment_id)
    assert fetched.name == name
    assert str(fetched.id) == str(deployment_id)

    listed = {str(d.id) for d in client.apps.list_deployments()}
    assert str(deployment_id) in listed, (
        "a live deployment did not appear in list_deployments"
    )

    # Instances are the platform's own account of what is running, and a status
    # string alone is not proof the deploy did anything — so this asserts there
    # is at least one. A type check would pass on an empty list, which is
    # exactly the state this station exists to rule out.
    instances = client.apps.list_instances(deployment_id)
    assert instances, (
        f"deployment {name} reports {status!r} but the platform lists no "
        "instances for it; a status field is not proof the deploy ran"
    )
    # Correlate: a filter the platform ignored would otherwise let an unrelated
    # instance from this shared host satisfy the claim that THIS deploy ran.
    foreign = [
        str(i.deployment_id)
        for i in instances
        if str(i.deployment_id) != str(deployment_id)
    ]
    assert not foreign, (
        f"list_instances({deployment_id}) returned instances belonging to other "
        f"deployments {foreign}; the filter cannot be relied on, so an unrelated "
        "instance would have satisfied this station"
    )
    # A row is not a running workload. AppInstance.status defaults to
    # UNINITIALIZED, so correctly correlated rows that are all FAILED or STOPPED
    # would otherwise satisfy this station while nothing runs.
    #
    # Not bounded by the deployment status this test already polled: the platform
    # keeps the two independent and checks both itself before treating a
    # deployment as usable -- `dep.status == "DEPLOYED" and any(inst.status ==
    # "DEPLOYED" for inst in dep.instances)` in the platform's own readiness
    # filter (kamiwaza/serving/garden/apps/apps.py).
    live = [i for i in instances if i.status in RUNNING_STATUSES]
    assert live, (
        f"deployment {name} reports a running deployment status, but none of its "
        f"{len(instances)} instance(s) is in {sorted(RUNNING_STATUSES)}: "
        f"{[i.status for i in instances]}"
    )


def _assert_retirement_station(client, deployment_id: UUID, name: str) -> None:
    """Stop is a station of the capability, so the test asserts it.

    Not left to the teardown finalizer: that suppresses errors by design (it must
    run after a failed body) and purges independently, so a broken
    ``stop_deployment`` followed by a successful purge would leave teardown green
    and publish stop-lifecycle evidence that nothing established.
    """
    assert client.apps.stop_deployment(deployment_id), (
        f"stop_deployment({deployment_id}) did not report success for {name}"
    )
    deadline = time.monotonic() + 300
    status: str | None = None
    while True:
        try:
            status = client.apps.get_deployment_status(deployment_id)
        except APIError:
            # The platform may reap a stopped deployment's row, and then answers
            # a status lookup for it with an error. That is a completed stop --
            # but only when the row really is gone: an error raised while the row
            # is still listed is a genuine fault, and is re-raised rather than
            # read as a stop.
            if _is_listed(client, name):
                raise
            return
        if status in STOPPED_STATUSES:
            return
        # The other way a stop completes: the row was reaped between polls.
        # Checked after the status poll, so a deployment sitting in
        # STOP_REQUESTED keeps waiting here rather than passing the station.
        if not _is_listed(client, name):
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(5)
    pytest.fail(
        f"deployment {name} did not stop within 300s of stop_deployment: its row "
        f"is still listed with status {status!r}. STOP_REQUESTED records only "
        "that the request was accepted, so the stop station is unproven"
    )


def _wait_for_settled_status(
    client, deployment_id: UUID, *, timeout: float = 600
) -> str:
    """Poll until the deployment settles, returning the status it settled in."""
    deadline = time.monotonic() + timeout
    status = client.apps.get_deployment_status(deployment_id)
    while time.monotonic() < deadline:
        if status in SETTLED_STATUSES:
            return status
        time.sleep(10)
        status = client.apps.get_deployment_status(deployment_id)
    # NOT _skip_or_fail: the deployment request was accepted, so failure to
    # settle is a failure of the mapped deploy/poll lifecycle rather than an
    # absent prerequisite. Skipping here would let a real capability failure
    # pass as an under-provisioned host whenever KZ_REQUIRE_* is unset.
    pytest.fail(
        f"deployment {deployment_id} never settled within {timeout}s "
        f"(last status {status!r}); the deploy station did not complete"
    )
