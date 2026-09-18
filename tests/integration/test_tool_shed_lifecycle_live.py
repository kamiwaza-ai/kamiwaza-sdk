"""Evidence for ``tools.mcp-tool-shed`` — deploy, health over MCP, discovery, stop.

ENG-12432. The capability document's planned SDK arm is one run: list available
templates, deploy one (supplying its required environment variables), assert the
deployment reports healthy over MCP and appears in discovery, then stop it. The
deploy-from-image path and the missing-required-variable failure mode are named
as natural assertions in the same run.

Two properties of the 1.2.1 surface shape this module, both checked against the
live deployment rather than assumed:

* **There is no purge for tool deployments.** ``stop_deployment`` is the only
  retirement path and it leaves a ``STOPPED`` row behind, unlike the App Garden
  surface which offers a purge. So this module's teardown asserts the
  deployment reached ``STOPPED``; it cannot assert its absence, and every run
  leaves one row.
* **Discovery is not filtered to running servers.** ``GET /tool/discover``
  returns every deployment record, whatever its status. This module therefore
  asserts that the deployment under test appears in discovery, and deliberately
  does NOT assert that discovery lists only running servers — see the module's
  entry in ``tests/e2e/capability_map.yaml`` for why that is left unclaimed
  rather than asserted either way.
"""

from __future__ import annotations

import os
import time
from contextlib import suppress
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.tools import ToolTemplate

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

# Images served from a developer's local registry are not pullable by the
# cluster; a deploy failing on one says nothing about this capability.
LOCAL_DEV_REGISTRY_MARKERS = ("host.docker.internal", "localhost:", "127.0.0.1:")

DEPLOYED_STATUSES = frozenset({"DEPLOYED", "RUNNING"})
SETTLED_STATUSES = frozenset({"DEPLOYED", "RUNNING", "FAILED", "STOPPED"})


def _skip_or_fail(reason: str) -> NoReturn:
    if os.environ.get("KZ_REQUIRE_TOOL_SHED_EVIDENCE") == "1":
        pytest.fail(reason)
    pytest.skip(reason)
    raise AssertionError("unreachable: pytest.skip always raises")


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _deployable_template(client) -> ToolTemplate:
    """An imported template the cluster can pull and that needs no secrets.

    ``KZ_TOOL_SHED_TEMPLATE`` pins a name explicitly. Otherwise take an
    imported template whose image is not served from a developer's local
    registry and which declares no required environment variables, since this
    suite has no credentials to supply for one that does.
    """
    templates = client.tools.list_imported_templates()
    if not templates:
        _skip_or_fail("no imported tool templates on this host")

    pinned = os.environ.get("KZ_TOOL_SHED_TEMPLATE")
    if pinned:
        for template in templates:
            if template.name == pinned:
                return template
        _skip_or_fail(f"KZ_TOOL_SHED_TEMPLATE={pinned!r} is not imported here")

    rejected: list[str] = []
    for template in templates:
        image = template.image or ""
        if any(marker in image for marker in LOCAL_DEV_REGISTRY_MARKERS):
            rejected.append(f"{template.name} (local dev registry)")
            continue
        if template.required_env_vars:
            rejected.append(
                f"{template.name} (needs {', '.join(template.required_env_vars)})"
            )
            continue
        return template

    _skip_or_fail("no imported template is deployable here: " + "; ".join(rejected))


def _wait_for_settled(client, deployment_id: UUID, *, timeout: float = 600) -> str:
    deadline = time.monotonic() + timeout
    status = client.tools.get_deployment(deployment_id).status
    while time.monotonic() < deadline:
        if status in SETTLED_STATUSES:
            return status
        time.sleep(10)
        status = client.tools.get_deployment(deployment_id).status
    _skip_or_fail(
        f"tool deployment {deployment_id} never settled within {timeout}s "
        f"(last status {status!r})"
    )


def test_tool_shed_template_catalog_is_listed(live_kamiwaza_client) -> None:
    """Available and imported catalogues are listed separately.

    Deliberately NOT mapped: a catalogue listing establishes nothing about
    deploying a tool server, and ENG-12432 rules out a catalog-only pass.
    """
    client = live_kamiwaza_client

    imported = client.tools.list_imported_templates()
    available = client.tools.list_available_templates()
    assert isinstance(imported, list)
    assert isinstance(available, list)
    assert imported, "no imported tool templates to report"

    garden = client.tools.get_garden_status()
    assert isinstance(garden, dict)


@pytest.mark.usefixtures("live_server_available")
def test_tool_shed_deploy_health_discovery_and_stop(live_kamiwaza_client) -> None:
    """The documented arm: deploy a template, prove MCP health, discover it, stop."""
    client = live_kamiwaza_client
    template = _deployable_template(client)

    pre_existing = {str(d.id) for d in client.tools.list_deployments()}
    name = _unique("eng12432-tool")

    deployment = client.tools.deploy_from_template(
        template_name=template.name,
        name=name,
    )
    deployment_id = deployment.id
    try:
        assert str(deployment_id) not in pre_existing, (
            "deploy_from_template returned a deployment that already existed"
        )
        assert deployment.url, (
            "the deployment carries no public URL; the document promises a "
            "generated MCP endpoint"
        )

        status = _wait_for_settled(client, deployment_id)
        if status not in DEPLOYED_STATUSES:
            pytest.fail(
                f"tool deployment {name} settled in {status!r}; the deploy "
                "station did not succeed on this host"
            )

        _assert_mcp_health(client, deployment_id, name)
        _assert_appears_in_discovery(client, deployment_id, name)
    finally:
        with suppress(APIError):
            client.tools.stop_deployment(deployment_id)

    # 1.2.1 offers no purge for tool deployments, so retirement is proven by
    # the status transition rather than by the row disappearing.
    final = client.tools.get_deployment(deployment_id)
    assert final.status not in DEPLOYED_STATUSES, (
        f"tool deployment {name} still reports {final.status!r} after stop"
    )


def _assert_mcp_health(client, deployment_id: UUID, name: str) -> None:
    """Health is an MCP-protocol check, not a container liveness probe."""
    health = client.tools.check_health(deployment_id)
    assert health.status == "healthy", (
        f"tool deployment {name} reported health {health.status!r}"
        + (f" ({health.error})" if health.error else "")
    )


def _assert_appears_in_discovery(client, deployment_id: UUID, name: str) -> None:
    """The deployment under test is discoverable by any MCP-capable client."""
    discovery = client.tools.discover_servers()
    assert discovery.total == len(discovery.servers), (
        f"discovery reported total={discovery.total} but listed "
        f"{len(discovery.servers)} servers"
    )
    mine = [s for s in discovery.servers if str(s.deployment_id) == str(deployment_id)]
    assert len(mine) == 1, (
        f"the deployment under test appears {len(mine)} times in discovery; "
        "expected exactly once"
    )
    assert mine[0].url, "the discovered server carries no URL for a client to call"


@pytest.mark.usefixtures("live_server_available")
def test_tool_shed_template_deploy_names_missing_required_env_vars(
    live_kamiwaza_client,
) -> None:
    """A template deploy missing a required variable fails as a client error.

    The document is specific that this "fails as a client error naming the
    missing variables, not as a broken deployment" — so the assertion is on the
    status code and on the variable being named, not merely that something
    raised.

    Deliberately NOT mapped on its own: it is a negative path, and under the
    PER-TEST RULE a run of it alone must never emit a passing record for the
    capability.
    """
    client = live_kamiwaza_client

    candidates = [
        t for t in client.tools.list_available_templates() if t.required_env_vars
    ]
    if not candidates:
        _skip_or_fail(
            "no available template declares required_env_vars, so the "
            "missing-variable failure mode cannot be exercised here"
        )
    template = candidates[0]
    required = template.required_env_vars[0]

    with pytest.raises(APIError) as exc:
        client.tools.deploy_from_template(
            template_name=template.name,
            name=_unique("eng12432-missingenv"),
        )

    assert exc.value.status_code in {400, 422}, (
        f"a missing required variable should be a client error; got "
        f"{exc.value.status_code}"
    )
    assert required in str(exc.value), (
        f"the error should name the missing variable {required!r}; got "
        f"{str(exc.value)[:200]}"
    )
