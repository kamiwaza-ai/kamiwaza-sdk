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
SETTLED_STATUSES = frozenset({"DEPLOYED", "FAILED", "STOP_REQUESTED", "STOPPED"})
RUNNING_STATUSES = frozenset({"DEPLOYED", "RUNNING"})

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


def _purge(client, deployment_id: UUID) -> None:
    """Remove the deployment row, not merely stop it.

    ``stop_deployment`` leaves a ``STOPPED`` row behind. A test that only
    stopped would accumulate residue on shared infrastructure every run.
    """
    with suppress(APIError):
        client.delete(f"/apps/deployment/{deployment_id}/purge")


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
    live_kamiwaza_client,
) -> None:
    """The documented arm: resolve, deploy, assert the key boundary, poll, retire."""
    client = live_kamiwaza_client
    template = _deployable_template(client)

    pre_existing = {str(d.id) for d in client.apps.list_deployments()}
    sentinel = uuid4().hex
    name = _unique("eng12432-garden")

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
    try:
        # A pre-existing row must never be mistaken for a successful deploy.
        assert str(deployment_id) not in pre_existing, (
            "deploy returned a deployment that already existed before the call"
        )

        _assert_reserved_key_boundary(client, deployment_id, sentinel)
        _assert_monitoring_stations(client, deployment_id, name)
    finally:
        with suppress(APIError):
            client.apps.stop_deployment(deployment_id)
        _purge(client, deployment_id)

    # Retire is proven, not assumed: the row is gone from the listing.
    remaining = {str(d.id) for d in client.apps.list_deployments()}
    assert str(deployment_id) not in remaining, (
        f"deployment {deployment_id} survived stop + purge"
    )


def _assert_reserved_key_boundary(client, deployment_id: UUID, sentinel: str) -> None:
    """The caller's value is ignored for one key and preserved for the others."""
    stored = client.apps.get_deployment(deployment_id)
    env = stored.env_vars or {}
    assert env, "the deployment read-back exposed no env_vars to assert against"

    assert RESERVED_PROBE_KEY in env, (
        f"{RESERVED_PROBE_KEY} should be present in the read-back with a "
        f"platform-supplied value; got keys {sorted(env)[:12]}"
    )
    assert env[RESERVED_PROBE_KEY] != f"reserved-{sentinel}", (
        f"the caller-supplied value for {RESERVED_PROBE_KEY} survived into "
        "the deployment environment"
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
    assert status not in {"FAILED"}, (
        f"deployment {name} reached FAILED; the deploy station did not succeed"
    )

    fetched = client.apps.get_deployment(deployment_id)
    assert fetched.name == name
    assert str(fetched.id) == str(deployment_id)

    listed = {str(d.id) for d in client.apps.list_deployments()}
    assert str(deployment_id) in listed, (
        "a live deployment did not appear in list_deployments"
    )

    # Instances are the platform's own account of what is running. A status
    # string alone is not proof the deploy did anything.
    if status in RUNNING_STATUSES:
        instances = client.apps.list_instances(deployment_id)
        assert isinstance(instances, list)


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
    _skip_or_fail(
        f"deployment {deployment_id} never settled within {timeout}s "
        f"(last status {status!r}); the deploy station is unproven on this host"
    )
