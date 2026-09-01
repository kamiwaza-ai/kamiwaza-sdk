from __future__ import annotations

import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prepare_diffusion_live.sh"


def _executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_platform(tmp_path: Path) -> Path:
    platform = tmp_path / "kamiwaza"
    runtime = platform / "engine-images" / "diffusion" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "requirements-minimal.txt").touch()
    (runtime / "requirements.txt").touch()
    _executable(
        platform / "engine-images" / "diffusion" / "build.sh",
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "REGISTRY=$REGISTRY" "VERSION=$DIFFUSION_ENGINE_VERSION" "ARGS=$*" > "$FAKE_BUILD_RECORD"\n',
    )
    _executable(platform / "diffusion-venv" / "bin" / "python", "#!/bin/sh\nexit 0\n")
    return platform


def _fake_path(tmp_path: Path) -> Path:
    commands = tmp_path / "commands"
    _executable(
        commands / "uname",
        "#!/bin/sh\n"
        'case "$1" in -s) echo "$FAKE_UNAME_S" ;; -m) echo "$FAKE_UNAME_M" ;; esac\n',
    )
    for name in ("curl", "docker", "uv"):
        _executable(commands / name, "#!/bin/sh\nexit 0\n")
    _executable(commands / "git", "#!/bin/sh\necho deadbeefcafe\n")
    return commands


def _source(tmp_path: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    platform = _fake_platform(tmp_path)
    commands = _fake_path(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{commands}{os.pathsep}{env['PATH']}",
            "KAMIWAZA_PLATFORM_ROOT": str(platform),
            "FAKE_UNAME_S": "Linux",
            "FAKE_UNAME_M": "x86_64",
            "FAKE_BUILD_RECORD": str(tmp_path / "build-record"),
        }
    )
    env.update(overrides)
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; rc=$?; printf "rc=%s\\nbackend=%s\\nimage=%s\\n" "$rc" "${KAMIWAZA_TEST_DIFFUSION_BACKEND:-}" "${KAMIWAZA_TEST_DIFFUSION_IMAGE:-}"',
            "prepare-diffusion-test",
            str(SCRIPT),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_script_requires_sourcing() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT)], check=False, capture_output=True, text=True
    )

    assert result.returncode == 2
    assert "Source this script" in result.stderr


def test_darwin_prepares_host_runtime(tmp_path: Path) -> None:
    result = _source(tmp_path, FAKE_UNAME_S="Darwin", FAKE_UNAME_M="arm64")

    assert result.returncode == 0
    assert "rc=0\nbackend=auto\nimage=\n" in result.stdout
    assert "Prepared host diffusion runtime" in result.stdout


def test_linux_uses_explicit_fleet_image_without_building(tmp_path: Path) -> None:
    image = "fleet.example/diffusion:rocm"
    result = _source(
        tmp_path,
        KAMIWAZA_TEST_DIFFUSION_BACKEND="rocm",
        KAMIWAZA_TEST_DIFFUSION_IMAGE=image,
    )

    assert result.returncode == 0
    assert f"rc=0\nbackend=rocm\nimage={image}\n" in result.stdout
    assert not (tmp_path / "build-record").exists()


def test_linux_builds_commit_addressed_cpu_image(tmp_path: Path) -> None:
    result = _source(tmp_path)

    expected = (
        "host.docker.internal:5001/kamiwaza-uat/"
        "diffusion-engine:cpu-uat-deadbeefcafe-amd64"
    )
    assert result.returncode == 0
    assert f"rc=0\nbackend=cpu\nimage={expected}\n" in result.stdout
    record = (tmp_path / "build-record").read_text(encoding="utf-8")
    assert "REGISTRY=localhost:5001/kamiwaza-uat" in record
    assert "VERSION=uat-deadbeefcafe-amd64" in record
    assert "ARGS=cpu --push --platform linux/amd64" in record
