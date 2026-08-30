"""Offline contracts for the gated-retrieval live-rig provisioner."""

from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.integration import _gate_fixture as fixture

pytestmark = pytest.mark.unit


def test_dataset_uses_the_always_mounted_allowed_root() -> None:
    assert fixture.DATASET_PATH == "/app/tmp/eng10050-mini-clearance.csv"


def _completed(
    cmd: list[str],
    returncode: int = 0,
    *,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        (["kubectl", "get", "pods"], ["kubectl", "get", "pods"]),
        (
            ["ssh", "spark-2", "kubectl", "get", "pods", "-o", "jsonpath={.items[0]}"],
            ["ssh", "spark-2", "kubectl get pods -o 'jsonpath={.items[0]}'"],
        ),
    ],
)
def test_run_quotes_only_the_ssh_remote_half(
    monkeypatch: pytest.MonkeyPatch,
    cmd: list[str],
    expected: list[str],
) -> None:
    observed: dict[str, Any] = {}

    def fake_run(actual: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed.update(cmd=actual, kwargs=kwargs)
        return _completed(actual)

    monkeypatch.setattr(fixture.subprocess, "run", fake_run)

    result = fixture.run(cmd, cwd="/tmp/work")

    assert result.returncode == 0
    assert observed["cmd"] == expected
    assert observed["kwargs"] == {
        "capture_output": True,
        "text": True,
        "timeout": 180,
        "cwd": "/tmp/work",
    }


def _stage_build_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text("[build-system]\n")
    (source / "module.py").write_text("VALUE = 1\n")

    stage = tmp_path / "stage"
    monkeypatch.setattr(fixture, "STAGE", stage)
    monkeypatch.setattr(fixture, "WHEEL_DIR", stage / "wheels")
    return source


def test_build_wheel_falls_back_to_pip_and_hashes_exact_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _stage_build_paths(monkeypatch, tmp_path)
    wheel_bytes = b"exact-wheel-bytes\x00\xff"
    calls: list[list[str]] = []

    monkeypatch.setattr(fixture.shutil, "which", lambda command: f"/bin/{command}")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        assert kwargs["cwd"] == fixture.STAGE / "src"
        if cmd[0] == "uv":
            return _completed(cmd, 1, stderr="uv failed")
        fixture.WHEEL_DIR.joinpath(fixture.WHEEL_NAME).write_bytes(wheel_bytes)
        return _completed(cmd)

    monkeypatch.setattr(fixture, "run", fake_run)

    wheel, digest = fixture.build_wheel(source)

    assert [cmd[0] for cmd in calls] == ["uv", sys.executable]
    assert wheel == fixture.WHEEL_DIR / fixture.WHEEL_NAME
    assert wheel.read_bytes() == wheel_bytes
    assert digest == f"sha256:{hashlib.sha256(wheel_bytes).hexdigest()}"


def test_build_wheel_reports_all_failed_attempts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _stage_build_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(fixture.shutil, "which", lambda command: f"/bin/{command}")

    def fail(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return _completed(cmd, 1, stderr=f"{Path(cmd[0]).name} failed")

    monkeypatch.setattr(fixture, "run", fail)

    with pytest.raises(SystemExit) as exc_info:
        fixture.build_wheel(source)

    message = str(exc_info.value)
    assert "wheel build failed" in message
    assert "uv failed" in message
    assert "python" in message


def test_build_wheels_materializes_lifecycle_and_federation_versions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _stage_build_paths(monkeypatch, tmp_path)
    calls: list[tuple[str, str]] = []

    def fake_build_version(
        src: Path, version: str, stage_name: str
    ) -> tuple[Path, str]:
        calls.append((version, stage_name))
        wheel = fixture.WHEEL_DIR / fixture.WHEEL_NAMES[version]
        wheel.parent.mkdir(parents=True, exist_ok=True)
        wheel.write_bytes(version.encode())
        return wheel, f"sha256:{version}"

    monkeypatch.setattr(fixture, "_build_version", fake_build_version)

    result = fixture.build_wheels(source)

    assert list(result) == list(fixture.PACKAGE_VERSIONS)
    assert calls == [
        (version, f"src-{version}") for version in fixture.PACKAGE_VERSIONS
    ]
    assert sorted(path.name for path, _ in result.values()) == sorted(
        fixture.WHEEL_NAMES.values()
    )


def test_auto_provision_refreshes_runtime_for_explicit_kubectl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "M5_TEST_WHEEL_DIR": "/tmp/wheels",
        "M5_TEST_INDEX_URL": "file:///fixture/simple",
        "M5_TEST_NETWORK_POLICY_ALLOWED_URL": "http://ray-head:18080/simple/",
        "MINI_CLEARANCE_DATASET_PATH": "/app/tmp/mini-clearance.csv",
    }
    observed: list[list[str]] = []

    def fake_provision(argv: list[str]) -> dict[str, str]:
        observed.append(argv)
        return expected

    monkeypatch.setenv("M5_TEST_KUBECTL", "ssh spark-4 kubectl")
    monkeypatch.setattr(fixture, "provision", fake_provision)

    assert fixture.auto_provision_from_env() == expected
    assert observed == [["ssh", "spark-4", "kubectl"]]


def test_auto_provision_preserves_manual_fixture_mode_without_kubectl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("M5_TEST_KUBECTL", raising=False)
    monkeypatch.setattr(
        fixture,
        "provision",
        lambda _argv: pytest.fail("manual fixture mode must not provision"),
    )

    assert fixture.auto_provision_from_env() == {}


def test_publish_routes_sorted_binary_files_over_ssh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "index"
    directory.mkdir()
    contents = {
        fixture.WHEEL_NAME: b"wheel\x00bytes",
        "index.html": b"<a>index</a>",
        "mini_clearance.csv": b"id,clearance\n1,high\n",
    }
    for name, payload in contents.items():
        directory.joinpath(name).write_bytes(payload)

    setup_calls: list[list[str]] = []

    def fake_fixture_run(
        cmd: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        setup_calls.append(cmd)
        if "jsonpath={.items[0].metadata.name}" in cmd:
            stdout = "ray-head-0"
        else:
            stdout = ""
        return _completed(cmd, stdout=stdout)

    writes: list[tuple[list[str], bytes]] = []

    def fake_subprocess_run(
        cmd: list[str], *, input: bytes, **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        writes.append((cmd, input))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(fixture, "run", fake_fixture_run)
    monkeypatch.setattr(fixture.subprocess, "run", fake_subprocess_run)

    assert fixture.publish(["ssh", "spark-2", "kubectl"], directory) == (
        "http://core-raycluster-head-svc.kamiwaza.svc.cluster.local:18080/simple/acme-gates/"
    )

    assert len(setup_calls) == 3
    assert "http.server" in setup_calls[2][-1]
    assert not any("jsonpath={.status.podIP}" in call for call in setup_calls)
    assert [base64.b64decode(payload) for _, payload in writes] == [
        contents[name] for name in sorted(contents)
    ]
    destinations = [cmd[2] for cmd, _ in writes]
    assert f"{fixture.MOUNT}/simple/acme-gates/{fixture.WHEEL_NAME}" in destinations[0]
    assert f"{fixture.MOUNT}/simple/acme-gates/index.html" in destinations[1]
    assert fixture.DATASET_PATH in destinations[2]
    assert all(cmd[:2] == ["ssh", "spark-2"] for cmd, _ in writes)


def test_publish_surfaces_the_failed_item(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "index"
    directory.mkdir()
    directory.joinpath("index.html").write_bytes(b"index")

    def fake_fixture_run(
        cmd: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        stdout = "ray-head-0" if "jsonpath={.items[0].metadata.name}" in cmd else ""
        return _completed(cmd, stdout=stdout)

    def fail_write(
        cmd: list[str], *, input: bytes, **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1, b"", b"permission denied")

    monkeypatch.setattr(fixture, "run", fake_fixture_run)
    monkeypatch.setattr(fixture.subprocess, "run", fail_write)

    with pytest.raises(
        SystemExit, match="writing index.html failed: permission denied"
    ):
        fixture.publish(["kubectl"], directory)


def test_publish_retries_a_truncated_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    item = tmp_path / "acme-gates.whl"
    item.write_bytes(b"exact wheel bytes")
    attempts: list[list[str]] = []

    def truncate_once(
        cmd: list[str], *, input: bytes, **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        attempts.append(cmd)
        assert hashlib.sha256(item.read_bytes()).hexdigest() in cmd[-1]
        assert ".partial" in cmd[-1]
        returncode = 74 if len(attempts) == 1 else 0
        stderr = b"published digest mismatch" if returncode else b""
        return subprocess.CompletedProcess(cmd, returncode, b"", stderr)

    monkeypatch.setattr(fixture.subprocess, "run", truncate_once)

    fixture._publish_item(["kubectl"], "ray-head-0", item, "/fixture")

    assert len(attempts) == 2


def test_verify_hashes_the_published_wheel_in_the_ray_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = "a" * 64
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if "jsonpath={.items[0].metadata.name}" in cmd:
            return _completed(cmd, stdout="ray-head-0")
        return _completed(cmd, stdout=f"{expected}  {fixture.WHEEL_NAME}\n")

    monkeypatch.setattr(fixture, "run", fake_run)

    fixture.verify(["kubectl"], f"sha256:{expected}")

    assert len(calls) == 2
    assert "ray-head-0" in calls[1]
    assert calls[1][-2:] == [
        "sha256sum",
        f"{fixture.MOUNT}/simple/acme-gates/{fixture.WHEEL_NAME}",
    ]


def test_teardown_removes_only_the_owned_fixture_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        stdout = "ray-head-0" if "jsonpath={.items[0].metadata.name}" in cmd else ""
        return _completed(cmd, stdout=stdout)

    monkeypatch.setattr(fixture, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["_gate_fixture", "teardown"])

    assert fixture.main() == 0
    assert calls[-1][-5:] == [
        "--",
        "rm",
        "-rf",
        fixture.MOUNT,
        fixture.DATASET_PATH,
    ]
    assert "kamiwaza-gate-index.pid" in calls[-2][-1]


def test_gate_fixture_source_is_owned_by_sdk() -> None:
    source = fixture.locate_source()

    assert source == (
        Path(fixture.REPO) / "tests" / "integration" / "fixtures" / "acme-gates"
    )
    assert (source / "pyproject.toml").is_file()
    assert (source / "acme_gates" / "gate.py").is_file()
    assert (source / "acme_gates" / "exec_gate.py").is_file()
    assert (source / "acme_gates" / "mini_clearance_gate.py").is_file()
