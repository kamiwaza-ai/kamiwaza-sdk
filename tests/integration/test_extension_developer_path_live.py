"""Evidence for ``extensions.developer-path`` — the kz-ext toolchain path.

ENG-12432. The capability document asks whether a customer's own developers can
extend the platform. Its guarantees are scaffolding provided by the toolchain,
an iterate loop that really builds and pushes, a published extension becoming a
platform resource, and a documented guide.

This module covers the stations reachable from a test process:

* **Scaffolding** — ``kz-ext create`` produces a complete extension project
  whose manifest is the input the deploy stage consumes.
* **Platform resource** — an extension created through the SDK is listable,
  reports status, and is removable, with absence proven rather than assumed.

The ``kz-ext dev`` deploy is **not** covered here. It is declared as an
unverified operation on this capability's ``capability_map.yaml`` entry, so the
emitted record derives ``failed`` with a named skipped step rather than claiming
a path whose final stage was never exercised. The reason it could not be
exercised on the evidence host is recorded with the staged evidence, which is
internal; this module stays to test mechanics.

Distinct from ``test_extensions_live.py``, which exercises the extension API's
CRUD and error paths without involving the toolchain at all. A capability about
``kz-ext`` cannot be established by API calls that never invoke it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

import pytest
import yaml
from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.extensions import (
    CreateExtension,
    ExtensionPort,
    ExtensionServiceSpec,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

# Manifest keys the deploy stage reads. Asserting them keeps this test honest
# about what the scaffold hands the next station, without restating the whole
# template.
# Manifest keys the deploy stage reads, with the type each must carry. A
# presence check alone admits [] or {}, which are neither None nor "" and are
# just as unconsumable.
MANIFEST_KEYS_THE_DEPLOY_CONSUMES = {
    "name": str,
    "version": str,
    "type": str,
    "kz_ext_version": str,
    "risk_tier": int,
}


def _skip_or_fail(reason: str) -> NoReturn:
    if os.environ.get("KZ_REQUIRE_EXTENSION_PATH_EVIDENCE") == "1":
        pytest.fail(reason)
    pytest.skip(reason)
    raise AssertionError("unreachable: pytest.skip always raises")


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _kz_ext() -> Path:
    kz_ext = Path(sys.executable).with_name("kz-ext")
    if not kz_ext.is_file():
        _skip_or_fail(f"kz-ext console script is absent beside {sys.executable}")
    return kz_ext


def _run(command: list[str], *, cwd: Path, timeout: int = 300) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Command failed ({result.returncode}) in {cwd}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def test_kz_ext_scaffolds_an_extension_the_deploy_stage_can_consume(
    tmp_path: Path,
) -> None:
    """The toolchain provides the scaffold, and it carries a deployable manifest.

    Not a duplicate of the app-generation module's scaffold test: that one
    asserts the generated application's shape, this one asserts the manifest
    contract between ``kz-ext create`` and the deploy stage that reads it.
    """
    _run(
        [str(_kz_ext()), "create", "--type", "app", "--name", "eng12432extpath"],
        cwd=tmp_path,
    )

    manifest_path = tmp_path / "kamiwaza.json"
    assert manifest_path.is_file(), "kz-ext create produced no kamiwaza.json"
    manifest = json.loads(manifest_path.read_text())
    for key, expected_type in MANIFEST_KEYS_THE_DEPLOY_CONSUMES.items():
        assert key in manifest, f"the manifest lacks {key!r}, which the deploy reads"
        value = manifest[key]
        # Presence is not consumability, and neither is "not empty": [] and {}
        # pass both while giving the deploy stage nothing it can use. bool is
        # excluded explicitly because it is a subclass of int.
        assert isinstance(value, expected_type) and not isinstance(value, bool), (
            f"the manifest's {key!r} is {value!r} ({type(value).__name__}); the "
            f"deploy stage needs a {expected_type.__name__}"
        )
        if isinstance(value, str):
            assert value.strip(), f"the manifest's {key!r} is blank"
    assert manifest["name"] == "eng12432extpath"
    assert manifest["type"] == "app"

    compose = tmp_path / "docker-compose.yml"
    assert compose.is_file(), "the deploy stage derives services from the compose file"
    services = (yaml.safe_load(compose.read_text()) or {}).get("services") or {}
    assert services, (
        "the generated compose file declares no services, so the deploy stage "
        "would have nothing to build"
    )


@pytest.fixture
def deleted_extensions(live_kamiwaza_client) -> Iterator[list[str]]:
    """Delete every extension this test names, and prove each one is gone.

    A fixture finalizer rather than a ``finally`` block: the name is registered
    before the creating call, so a create whose server side committed and whose
    response then raised is still reconciled; and a teardown failure surfaces as
    its own ERROR rather than masking the test's failure or being masked by it.
    """
    service = live_kamiwaza_client.extensions
    names: list[str] = []
    yield names

    survivors: list[str] = []
    for name in names:
        with suppress(APIError):
            service.delete_extension(name)
        for _ in range(30):
            if name not in {ext.name for ext in service.list_extensions()}:
                break
            time.sleep(1)
        else:
            survivors.append(name)
    assert not survivors, (
        f"extensions remained listed after deletion and are leaked on a "
        f"shared host: {survivors}"
    )


@pytest.mark.usefixtures("live_server_available")
def test_a_published_extension_is_a_platform_resource(
    live_kamiwaza_client, deleted_extensions
) -> None:
    """Created through the SDK, listable, status-reporting, and removable.

    This is the capability document's "a published extension is a platform
    resource" guarantee, asserted through the two SDK entry points the document
    names: ``create_extension`` and ``get_extension_status``.
    """
    service = live_kamiwaza_client.extensions
    name = _unique("eng12432extpath")

    pre_existing = {ext.name for ext in service.list_extensions()}
    assert name not in pre_existing

    # Registered before the creating call, so a create whose server side
    # committed and whose response then raised is still reconciled by name.
    deleted_extensions.append(name)

    created = service.create_extension(
        CreateExtension(
            name=name,
            type="tool",
            version="0.0.1-eng12432",
            services=[
                ExtensionServiceSpec(
                    name="echo",
                    image="busybox:latest",
                    primary=True,
                    ports=[ExtensionPort(container_port=8080)],
                    command=[
                        "sh",
                        "-c",
                        "while true; do echo ok | nc -l -p 8080; done",
                    ],
                ),
            ],
        )
    )
    assert created.name == name

    listed = {ext.name for ext in service.list_extensions()}
    assert name in listed, "a created extension did not appear in the listing"

    status = service.get_extension_status(name)
    assert status.services, (
        "get_extension_status reported no services for the created extension"
    )
    assert any(item.name == "echo" for item in status.services), (
        f"the declared service is absent from status: "
        f"{[item.name for item in status.services]}"
    )
    # Removal is asserted by the `deleted_extensions` finalizer, which
    # reconciles by name and polls for absence. Asserting it inline would not
    # run when the body fails, which is exactly when a leak matters.
