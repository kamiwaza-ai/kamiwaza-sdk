"""End-to-end canary for the App Garden runtime-path scaffold.

The test intentionally starts at the public ``kz-ext create`` command and
builds from the generated extension directory.  This guards the boundary that
the old Copier repository used to own: bundled SDK templates, local-SDK build
overlays, the dual prebuilt Next artifacts, and startup-time byte relocation.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest
import yaml

from kamiwaza_extensions.sdk_override import (
    SdkOverrideSpec,
    apply_build_overlay,
    generate_build_overrides,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
TS_RUNTIME = REPO_ROOT / "kamiwaza-ai-extensions-lib"
SENTINEL = "/__KZ_RUNTIME_BASE_7F3A91C2__"


def _skip_or_fail(reason: str) -> NoReturn:
    if os.environ.get("KZ_REQUIRE_NEXT_RUNTIME_E2E") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


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
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Command failed ({result.returncode}) in {cwd}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def _run_cleanup(command: list[str]) -> None:
    subprocess.run(command, text=True, capture_output=True, timeout=60)


def _require_build_tools() -> None:
    missing = [tool for tool in ("docker", "npm") if shutil.which(tool) is None]
    if missing:
        _skip_or_fail(f"runtime-path canary requires: {', '.join(missing)}")
    docker_info = subprocess.run(
        ["docker", "info"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if docker_info.returncode != 0:
        _skip_or_fail(f"Docker daemon unavailable: {docker_info.stderr.strip()}")
    buildx = subprocess.run(
        ["docker", "buildx", "version"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if buildx.returncode != 0:
        _skip_or_fail("Docker Buildx is required for the local SDK build context")


def _isolate_public_docker_config(
    target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bypass Docker Desktop's helper for this public-only image pull."""
    configured_dir = Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker")))
    try:
        config = json.loads((configured_dir / "config.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return
    helpers = {
        config.get("credsStore", ""),
        *config.get("credHelpers", {}).values(),
    }
    if not any(str(helper).startswith("desktop") for helper in helpers):
        return

    target.mkdir()
    plugin_dir = target / "cli-plugins"
    plugin_dir.mkdir()
    plugin = shutil.which("docker-buildx")
    if plugin is None:
        user_plugin = Path.home() / ".docker" / "cli-plugins" / "docker-buildx"
        if user_plugin.exists():
            plugin = str(user_plugin)
    if plugin is not None:
        (plugin_dir / "docker-buildx").symlink_to(Path(plugin).resolve())
    monkeypatch.setenv("DOCKER_CONFIG", str(target))


def _scaffold_with_kz_ext(target: Path) -> None:
    kz_ext = Path(sys.executable).with_name("kz-ext")
    if not kz_ext.is_file():
        _skip_or_fail(f"kz-ext console script is absent beside {sys.executable}")
    _run(
        [str(kz_ext), "create", "--type", "app", "--name", "runtime-canary"],
        cwd=target,
        timeout=60,
    )
    _run([str(kz_ext), "validate", str(target)], cwd=target, timeout=60)


def _write_local_sdk_dockerfile(extension_dir: Path) -> Path:
    compose = yaml.safe_load((extension_dir / "docker-compose.yml").read_text())
    spec = SdkOverrideSpec(
        sdk_repo=REPO_ROOT,
        python=False,
        typescript=True,
    )
    override = next(
        item
        for item in generate_build_overrides(
            spec,
            compose,
            extension_dir=extension_dir,
        )
        if item.service_name == "frontend"
    )
    frontend = extension_dir / "frontend"
    patched = apply_build_overlay((frontend / "Dockerfile").read_text(), override)
    output = frontend / "Dockerfile.sdk-override"
    output.write_text(patched)
    return output


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _get(url: str, *, timeout: float = 3) -> tuple[int, bytes]:
    with urlopen(url, timeout=timeout) as response:
        return response.status, response.read()


def _wait_for_url(url: str, container: str, *, timeout: float = 45) -> bytes:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            status, body = _get(url)
            if status == 200:
                return body
            last_error = f"HTTP {status}"
        except (HTTPError, URLError, TimeoutError, ConnectionError) as error:
            last_error = str(error)
        time.sleep(0.25)
    logs = subprocess.run(
        ["docker", "logs", container],
        text=True,
        capture_output=True,
        timeout=30,
    )
    raise AssertionError(
        f"Timed out waiting for {url}: {last_error}\n"
        f"container stdout:\n{logs.stdout}\ncontainer stderr:\n{logs.stderr}"
    )


def _start_container(
    *,
    image: str,
    name: str,
    port: int,
    mode: str,
    app_path: str = "",
) -> None:
    command = [
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=128m",
        "--publish",
        f"127.0.0.1:{port}:3000",
        "--env",
        "HOSTNAME=0.0.0.0",
        "--env",
        f"KAMIWAZA_ROUTING_MODE={mode}",
        "--env",
        f"KAMIWAZA_APP_PATH={app_path}",
        "--env",
        f"KAMIWAZA_ORIGIN=http://127.0.0.1:{port}",
        image,
    ]
    _run(command, cwd=REPO_ROOT, timeout=60)


def _runtime_event(container: str, mode: str) -> tuple[dict[str, object], str]:
    logs = _run(["docker", "logs", container], cwd=REPO_ROOT, timeout=30)
    combined = f"{logs.stdout}\n{logs.stderr}"
    for line in combined.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "kz_next_runtime" and event.get("mode") == mode:
            return event, combined
    raise AssertionError(f"No {mode!r} kz_next_runtime event in logs:\n{combined}")


def _assert_path_runtime(image: str, container: str) -> None:
    port = _free_port()
    app_path = f"/runtime/apps/canary-{uuid.uuid4().hex[:12]}"
    _start_container(
        image=image,
        name=container,
        port=port,
        mode="path",
        app_path=app_path,
    )

    health = json.loads(
        _wait_for_url(f"http://127.0.0.1:{port}{app_path}/health", container)
    )
    assert health == {"status": "ok", "service": "frontend"}

    _, html_bytes = _get(f"http://127.0.0.1:{port}{app_path}")
    page = html_bytes.decode()
    assert SENTINEL not in page
    assert f"{app_path}/_next/" in page

    asset_match = re.search(
        rf"""(?:src|href)=["']({re.escape(app_path)}/_next/static/[^"']+)""",
        page,
    )
    assert asset_match is not None, "relocated page has no prefixed Next asset"
    asset_path = html.unescape(asset_match.group(1))
    asset_status, asset_body = _get(f"http://127.0.0.1:{port}{asset_path}")
    assert asset_status == 200
    assert len(asset_body) > 100

    _, runtime_body = _get(f"http://127.0.0.1:{port}{app_path}/kamiwaza/runtime.json")
    runtime = json.loads(runtime_body)
    assert runtime["routingMode"] == "path"
    assert runtime["appPath"] == app_path

    event, logs = _runtime_event(container, "path")
    assert event["appPath"] == app_path
    assert 0 <= int(event["prepare_ms"]) <= 5_000
    assert 0 < int(event["prepare_rss_mib"]) <= 96
    assert int(event["patched_files"]) > 0
    assert int(event["occurrences"]) > 0
    assert "next build" not in logs.lower()


def _assert_port_runtime(image: str, container: str) -> None:
    port = _free_port()
    _start_container(image=image, name=container, port=port, mode="port")

    health = json.loads(_wait_for_url(f"http://127.0.0.1:{port}/health", container))
    assert health == {"status": "ok", "service": "frontend"}
    _, runtime_body = _get(f"http://127.0.0.1:{port}/kamiwaza/runtime.json")
    runtime = json.loads(runtime_body)
    assert runtime["routingMode"] == "port"
    assert runtime["appPath"] == ""

    image_status, image_body = _get(
        f"http://127.0.0.1:{port}/_next/image?url=%2Fkmza-icon.png&w=32&q=75"
    )
    assert image_status == 200
    assert len(image_body) > 100

    event, logs = _runtime_event(container, "port")
    assert event["action"] == "start-native"
    assert "next build" not in logs.lower()


def _prepare_generated_frontend(extension_dir: Path) -> Path:
    _scaffold_with_kz_ext(extension_dir)
    # ``npm pack --ignore-scripts`` is what the real --sdk-repo overlay uses,
    # so build dist from this checkout before passing it as a named context.
    _run(["npm", "ci", "--no-audit", "--no-fund"], cwd=TS_RUNTIME)
    _run(["npm", "run", "build"], cwd=TS_RUNTIME)

    dockerfile = _write_local_sdk_dockerfile(extension_dir)
    patched = dockerfile.read_text()
    install = patched.index("RUN npm install --no-audit --no-fund")
    overlay = patched.index(
        "# --- SDK override: install local TypeScript runtime lib ---"
    )
    assert install < overlay < patched.index("FROM deps AS dev")
    return dockerfile


def _build_runtime_image(dockerfile: Path, image: str) -> None:
    _run(
        [
            "docker",
            "buildx",
            "build",
            "--load",
            "--progress",
            "plain",
            "--build-context",
            f"sdk={REPO_ROOT}",
            "--file",
            dockerfile.name,
            "--tag",
            image,
            ".",
        ],
        cwd=dockerfile.parent,
    )


def _assert_runner_layout(image: str) -> None:
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            image,
            "-c",
            (
                "test -f /app/runtime/port/server.js"
                " && test -f /app/runtime/path/server.js"
                " && test ! -e /app/src"
                " && test ! -e /app/package.json"
            ),
        ],
        cwd=REPO_ROOT,
        timeout=60,
    )


def test_fresh_kz_ext_app_relocates_prebuilt_next_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generated app must boot at an arbitrary path without compiling."""
    # The canary only pulls public base images. Isolate it from workstation
    # credential helpers: a wedged desktop keychain helper must not turn a
    # public-image metadata request into an unbounded test hang.
    _isolate_public_docker_config(tmp_path / ".docker-public", monkeypatch)

    _require_build_tools()
    dockerfile = _prepare_generated_frontend(tmp_path)
    suffix = uuid.uuid4().hex[:12]
    image = f"kz-sdk-runtime-canary:{suffix}"
    path_container = f"kz-runtime-path-{suffix}"
    port_container = f"kz-runtime-port-{suffix}"
    try:
        _build_runtime_image(dockerfile, image)
        _assert_runner_layout(image)
        _assert_path_runtime(image, path_container)
        _assert_port_runtime(image, port_container)
    finally:
        _run_cleanup(["docker", "rm", "--force", path_container, port_container])
        _run_cleanup(["docker", "image", "rm", "--force", image])
