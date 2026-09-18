"""Evidence for ``platform.app-generation-path`` — the cluster-free stations.

ENG-12432. The capability document names four stations on the generation path:
``kz-ext create`` (scaffold), ``kz-ext dev local`` (local loop),
``kz-ext validate``, and ``kz-ext dev`` (deploy to a connected instance). The
first three need Docker and a local checkout but **no Kamiwaza instance**, and
this module covers exactly those. The deploy station needs a reachable
instance and an image registry the cluster can pull from; it lives in its own
module so that a run without a cluster can never contribute a passing step to
a record that claims the whole path.

Skip-or-fail follows the convention in ``test_scaffolded_next_runtime.py``:
these skip on an under-provisioned host, but set
``KZ_REQUIRE_APP_GENERATION_E2E=1`` to turn every skip into a failure, which is
what an evidence capture run does.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from kamiwaza_extensions_lib import __version__ as runtime_lib_version
from packaging.specifiers import SpecifierSet
from packaging.version import Version

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]

# Distribution name as it appears in the generated backend requirements.
RUNTIME_LIB_DISTRIBUTION = "kamiwaza-extensions-lib"

# `kz-ext dev local` reports each service on its own line, e.g.
#   backend: http://localhost:55012
SERVICE_URL_RE = re.compile(r"^(?P<service>backend|frontend):\s+(?P<url>https?://\S+)$")


def _skip_or_fail(reason: str) -> NoReturn:
    if os.environ.get("KZ_REQUIRE_APP_GENERATION_E2E") == "1":
        pytest.fail(reason)
    pytest.skip(reason)
    raise AssertionError("unreachable: pytest.skip always raises")


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
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
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def _run_cleanup(command: list[str], *, cwd: Path) -> None:
    subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, timeout=300, check=False
    )


def _get(url: str, *, timeout: float = 10) -> tuple[int, bytes]:
    with urlopen(url, timeout=timeout) as response:
        return response.status, response.read()


def _get_json(url: str) -> dict:
    status, body = _get(url)
    assert status == 200, f"{url} returned {status}"
    return json.loads(body)


def _wait_for_url(url: str, *, timeout: float = 120) -> bytes:
    """Poll until the service actually answers, or fail with the last error.

    Docker binds the published port before the process inside is listening, so
    the first request after ``kz-ext dev local`` returns is routinely met with
    a connection reset rather than a refusal. Without this wait the test is a
    race that passes only when the image is cold enough for the build to have
    given the app a head start.
    """
    deadline = time.monotonic() + timeout
    last_error = "never attempted"
    while time.monotonic() < deadline:
        try:
            status, body = _get(url, timeout=5)
        except (URLError, ConnectionResetError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if status == 200:
                return body
            last_error = f"HTTP {status}"
        time.sleep(2)
    raise AssertionError(
        f"{url} never became ready within {timeout}s; last: {last_error}"
    )


def _kz_ext() -> Path:
    kz_ext = Path(sys.executable).with_name("kz-ext")
    if not kz_ext.is_file():
        _skip_or_fail(f"kz-ext console script is absent beside {sys.executable}")
    return kz_ext


@pytest.fixture(scope="module")
def scaffold(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A fresh ``kz-ext create --type app`` scaffold, built once per module."""
    if shutil.which("docker") is None:
        _skip_or_fail("the generation path requires docker on PATH")
    target = tmp_path_factory.mktemp("app-generation")
    _run(
        [str(_kz_ext()), "create", "--type", "app", "--name", "eng12432app"],
        cwd=target,
        timeout=120,
    )
    # The document's rule: a directory holding no visible files is scaffolded
    # into directly, rather than getting a subdirectory named for --name.
    return target


def test_kz_ext_create_scaffolds_a_platform_ready_app(scaffold: Path) -> None:
    """The scaffold is a working application, not a blank project."""
    manifest_path = scaffold / "kamiwaza.json"
    assert manifest_path.is_file(), "scaffold has no kamiwaza.json manifest"

    manifest = json.loads(manifest_path.read_text())
    for key in (
        "name",
        "version",
        "type",
        "visibility",
        "risk_tier",
        "kz_ext_version",
        "template_version",
        "template_file_hashes",
    ):
        assert key in manifest, f"manifest is missing the documented key {key!r}"
    assert manifest["type"] == "app"
    assert manifest["name"] == "eng12432app"

    for relative in (
        "backend/app/main.py",
        "backend/requirements.txt",
        "frontend",
        "docker-compose.yml",
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
    ):
        assert (scaffold / relative).exists(), f"scaffold is missing {relative}"

    assert (scaffold / ".git").is_dir(), "kz-ext create did not initialize git"

    _assert_runtime_pin_is_satisfiable_from_this_checkout(scaffold)


def test_kz_ext_validate_passes_on_a_fresh_scaffold(scaffold: Path) -> None:
    """Validate reports a pass, with the local-dev notes the document describes.

    The notes matter as much as the exit status: the capability document says
    validate passes on a fresh scaffold *with informational notes* about
    local-dev-only constructs. A validate that passed silently would mean the
    scaffold no longer carries the bind mounts the local loop needs.
    """
    result = _run([str(_kz_ext()), "validate"], cwd=scaffold, timeout=120)
    combined = f"{result.stdout}\n{result.stderr}"
    assert "Validation passed" in combined, (
        f"kz-ext validate did not report a pass; output was:\n{combined}"
    )
    lowered = combined.lower()
    assert "bind mount" in lowered, "validate lost the documented bind-mount note"
    assert "resource limits" in lowered, (
        "validate lost the documented resource-limits note"
    )


def _parse_service_urls(output: str) -> dict[str, str]:
    """Pull the auto-assigned service URLs out of ``kz-ext dev local`` output.

    Ports are auto-assigned rather than fixed (the capability document says so
    explicitly), so the URLs have to be read from the command's own report
    rather than guessed.
    """
    urls: dict[str, str] = {}
    for line in output.splitlines():
        match = SERVICE_URL_RE.match(line.strip())
        if match:
            urls[match.group("service")] = match.group("url")
    return urls


def test_kz_ext_dev_local_serves_the_scaffolded_app(scaffold: Path) -> None:
    """The local loop builds and serves both services against this checkout.

    This is the station that proves ``--sdk-repo`` does its job: the scaffold
    pins a runtime library the public index cannot supply, so a backend that
    imports the pinned version at all is evidence the local checkout was
    substituted into the image.
    """
    if shutil.which("docker") is None:
        _skip_or_fail("kz-ext dev local requires docker on PATH")

    try:
        result = _run(
            [
                str(_kz_ext()),
                "dev",
                "local",
                "--detach",
                "--sdk-repo",
                str(REPO_ROOT),
            ],
            cwd=scaffold,
            timeout=1800,
        )
        urls = _parse_service_urls(f"{result.stdout}\n{result.stderr}")
        assert {"backend", "frontend"} <= urls.keys(), (
            f"kz-ext dev local did not report both service URLs; parsed {urls!r}"
        )

        health = json.loads(_wait_for_url(f"{urls['backend']}/health"))
        assert health["status"] == "ok"
        assert health["app"] == "eng12432app"

        info = _get_json(f"{urls['backend']}/api/info")
        assert info["app_name"] == "eng12432app"
        # Without --auth the document specifies an anonymous session.
        assert info["use_auth"] is False, (
            "a dev-local run without --auth should report an anonymous session"
        )

        frontend_body = _wait_for_url(urls["frontend"])
        assert b"eng12432app" in frontend_body, (
            "the frontend did not render the scaffolded app name"
        )

        _assert_backend_image_carries_the_local_runtime_lib(scaffold)
    finally:
        _run_cleanup(["docker", "compose", "down", "-v"], cwd=scaffold)


def _assert_backend_image_carries_the_local_runtime_lib(scaffold: Path) -> None:
    """The running backend must import the version this checkout ships."""
    container = _compose_container_id(scaffold, "backend")
    probe = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "python",
            "-c",
            "import kamiwaza_extensions_lib as k; print(k.__version__)",
        ],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert probe.returncode == 0, (
        f"could not read the runtime lib version from the backend container: "
        f"{probe.stderr.strip()}"
    )
    assert probe.stdout.strip() == runtime_lib_version, (
        f"backend image carries kamiwaza_extensions_lib "
        f"{probe.stdout.strip()!r}, but this checkout ships "
        f"{runtime_lib_version!r} — --sdk-repo did not take effect"
    )
    _assert_runtime_lib_came_from_the_local_checkout(container)


def _assert_runtime_lib_came_from_the_local_checkout(container: str) -> None:
    """Version equality alone does not establish provenance.

    An index install of the same version would satisfy it, so the claim that
    ``--sdk-repo`` took effect needs evidence of *where* the module came from.
    The override works by putting the checkout on ``PYTHONPATH`` rather than
    installing a distribution, so the signal is the resolved module path: a
    module served from the mounted source does not live under ``site-packages``.
    """
    probe = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "python",
            "-c",
            "import kamiwaza_extensions_lib as k; print(k.__file__)",
        ],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert probe.returncode == 0, (
        f"could not resolve the runtime lib's module path: {probe.stderr.strip()}"
    )
    module_path = probe.stdout.strip()
    assert module_path, "the runtime lib reported no module path"
    assert "site-packages" not in module_path, (
        f"the runtime library resolves from {module_path!r}, which is an "
        "installed distribution rather than the mounted checkout — --sdk-repo "
        "did not take effect, and the matching version only coincided"
    )


def _compose_container_id(scaffold: Path, service: str) -> str:
    result = _run(
        ["docker", "compose", "ps", "-q", service],
        cwd=scaffold,
        timeout=120,
    )
    container = result.stdout.strip().splitlines()
    assert len(container) == 1, (
        f"expected exactly one running {service} container, got {container!r}"
    )
    return container[0]


def _assert_runtime_pin_is_satisfiable_from_this_checkout(scaffold: Path) -> None:
    """The scaffold must pin a runtime library this checkout can actually supply.

    Asserted as a *relationship* rather than a literal: a literal pin rots on
    every bump, and this one already has. The capability document records
    ``kamiwaza-extensions-lib>=0.4.4,<0.5``; kz-ext 0.2.0 generates
    ``>=0.5,<0.6``, and 0.4.4 was never published to the public index at all
    (0.4.5 is the newest there). What matters for the generation path is not
    which numbers appear but whether ``--sdk-repo`` can satisfy the pin — the
    document's release-gap caveat is precisely that the public index cannot.
    """
    requirements = (scaffold / "backend" / "requirements.txt").read_text()
    pins = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip().startswith(RUNTIME_LIB_DISTRIBUTION)
    ]
    assert len(pins) == 1, (
        f"expected exactly one {RUNTIME_LIB_DISTRIBUTION} pin in the generated "
        f"backend requirements, found {pins!r}"
    )
    specifier = SpecifierSet(pins[0][len(RUNTIME_LIB_DISTRIBUTION) :])
    assert Version(runtime_lib_version) in specifier, (
        f"the scaffold pins {pins[0]!r}, which the runtime library in this "
        f"checkout ({runtime_lib_version}) does not satisfy — `kz-ext dev "
        "local --sdk-repo` could not build this backend"
    )
