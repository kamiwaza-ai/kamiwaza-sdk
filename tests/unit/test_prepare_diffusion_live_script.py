from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prepare_diffusion_live.sh"
RUNNER = Path(__file__).resolve().parents[2] / "scripts" / "run_diffusion_live.sh"


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
        'printf \'%s\\n\' "REGISTRY=$REGISTRY" "VERSION=$DIFFUSION_ENGINE_VERSION" "DOCKER_CONFIG=${DOCKER_CONFIG:-}" "ARGS=$*" > "$FAKE_BUILD_RECORD"\n',
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
    _executable(
        commands / "nvidia-smi",
        "#!/usr/bin/env bash\n"
        'if [[ "${1:-}" == "-L" ]]; then\n'
        '  for ((index=0; index < ${FAKE_NVIDIA_COUNT:-0}; index++)); do echo "GPU $index: fake"; done\n'
        "fi\n",
    )
    for name in ("curl", "uv"):
        _executable(commands / name, "#!/bin/sh\nexit 0\n")
    _executable(
        commands / "docker",
        "#!/bin/sh\n"
        'if [ -n "${FAKE_DOCKER_RECORD:-}" ]; then printf \'%s\\n\' "$*" >> "$FAKE_DOCKER_RECORD"; fi\n'
        "exit 0\n",
    )
    _executable(
        commands / "kubectl",
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$FAKE_KUBECTL_RECORD"\n'
        'if [[ "$*" == *"get configmap core-config"* ]]; then\n'
        '  cat "$FAKE_KUBECTL_STATE"\n'
        'elif [[ "$*" == *"get gateways.networking.istio.io"* ]]; then\n'
        "  printf '%s' \"${FAKE_GATEWAY_HOST:-}\"\n"
        'elif [[ "$*" == *"patch configmap core-config"* ]]; then\n'
        '  payload="${!#}"\n'
        '  jq -r \'.data.KAMIWAZA_INFERENCE_IMAGES\' <<<"$payload" > "${FAKE_KUBECTL_STATE}.new"\n'
        '  mv "${FAKE_KUBECTL_STATE}.new" "$FAKE_KUBECTL_STATE"\n'
        "fi\n",
    )
    _executable(commands / "git", "#!/bin/sh\necho deadbeefcafe\n")
    return commands


def _source(
    tmp_path: Path, *, cleanup: bool = False, **overrides: str
) -> subprocess.CompletedProcess[str]:
    platform = _fake_platform(tmp_path)
    commands = _fake_path(tmp_path)
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir()
    (docker_config / "config.json").write_text("{}", encoding="utf-8")
    kubectl_state = tmp_path / "kubectl-state"
    kubectl_state.write_text(
        '{"llamacpp":{"default":"registry.example/llamacpp:cpu"}}',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{commands}{os.pathsep}{env['PATH']}",
            "KAMIWAZA_PLATFORM_ROOT": str(platform),
            "FAKE_UNAME_S": "Linux",
            "FAKE_UNAME_M": "x86_64",
            "FAKE_BUILD_RECORD": str(tmp_path / "build-record"),
            "FAKE_DOCKER_RECORD": str(tmp_path / "docker-record"),
            "FAKE_KUBECTL_RECORD": str(tmp_path / "kubectl-record"),
            "FAKE_KUBECTL_STATE": str(kubectl_state),
            "KAMIWAZA_DIFFUSION_DOCKER_CONFIG": str(docker_config),
        }
    )
    env.update(overrides)
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; rc=$?; printf "rc=%s\\nbackend=%s\\nimage=%s\\nbase_url=%s\\n" "$rc" "${KAMIWAZA_TEST_DIFFUSION_BACKEND:-}" "${KAMIWAZA_TEST_DIFFUSION_IMAGE:-}" "${KAMIWAZA_BASE_URL:-}"; if [[ "$2" == cleanup ]]; then cleanup_diffusion_live; printf "cleanup_rc=%s\\n" "$?"; fi',
            "prepare-diffusion-test",
            str(SCRIPT),
            "cleanup" if cleanup else "no-cleanup",
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


def test_runner_owns_pytest_and_cleanup_lifecycle() -> None:
    content = RUNNER.read_text(encoding="utf-8")

    assert os.access(RUNNER, os.X_OK)
    assert "trap _cleanup_diffusion_live_on_exit EXIT" in content
    assert 'uv run pytest "${pytest_args[@]}"' in content
    assert "KAMIWAZA_DIFFUSION_JUNIT" in content
    assert "KAMIWAZA_TEST_DIFFUSION_ARTIFACT_DIR" in content
    assert "test_diffusion_qwen_live.py" in content
    assert "test_diffusion_qwen_split_live.py" in content
    assert "cleanup_diffusion_live || cleanup_rc=$?" in content
    assert 'exit "$cleanup_rc"' in content


def test_darwin_prepares_host_runtime(tmp_path: Path) -> None:
    result = _source(tmp_path, FAKE_UNAME_S="Darwin", FAKE_UNAME_M="arm64")

    assert result.returncode == 0
    assert "rc=0\nbackend=auto\nimage=\n" in result.stdout
    assert "Prepared host diffusion runtime" in result.stdout


def test_preparation_discovers_installed_gateway_host(tmp_path: Path) -> None:
    result = _source(
        tmp_path,
        FAKE_UNAME_S="Darwin",
        FAKE_UNAME_M="arm64",
        FAKE_GATEWAY_HOST="cornucopia.local",
    )

    assert result.returncode == 0
    assert "Discovered live Kamiwaza URL: https://cornucopia.local/api" in result.stdout
    assert "base_url=https://cornucopia.local/api" in result.stdout


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
    assert '"diffusion":{"default":"fleet.example/diffusion:rocm"}' in (
        tmp_path / "kubectl-state"
    ).read_text(encoding="utf-8")


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
    assert f"DOCKER_CONFIG={tmp_path / 'docker-config'}" in record
    assert "ARGS=cpu --platform linux/amd64" in record
    docker_record = (tmp_path / "docker-record").read_text(encoding="utf-8")
    assert "manifest inspect cgr.dev/kamiwaza/python:3.12-dev" in docker_record
    assert (
        "push localhost:5001/kamiwaza-uat/diffusion-engine:cpu-uat-deadbeefcafe-amd64"
    ) in docker_record


def test_linux_auto_selects_nvidia_image_when_host_has_gpus(tmp_path: Path) -> None:
    result = _source(tmp_path, FAKE_NVIDIA_COUNT="2")

    expected = (
        "host.docker.internal:5001/kamiwaza-uat/"
        "diffusion-engine:nvidia-uat-deadbeefcafe-amd64"
    )
    assert result.returncode == 0
    assert f"rc=0\nbackend=nvidia\nimage={expected}\n" in result.stdout
    record = (tmp_path / "build-record").read_text(encoding="utf-8")
    assert "ARGS=nvidia --platform linux/amd64" in record


def test_darwin_can_build_kubernetes_cpu_image(tmp_path: Path) -> None:
    result = _source(
        tmp_path,
        FAKE_UNAME_S="Darwin",
        FAKE_UNAME_M="arm64",
        KAMIWAZA_TEST_DIFFUSION_BACKEND="cpu",
    )

    expected = (
        "host.docker.internal:5001/kamiwaza-uat/"
        "diffusion-engine:cpu-uat-deadbeefcafe-arm64"
    )
    assert result.returncode == 0
    assert f"rc=0\nbackend=cpu\nimage={expected}\n" in result.stdout
    record = (tmp_path / "build-record").read_text(encoding="utf-8")
    assert "ARGS=cpu --platform linux/arm64" in record


def test_container_preparation_restores_trusted_cluster_catalog(tmp_path: Path) -> None:
    result = _source(tmp_path, cleanup=True)

    assert result.returncode == 0
    assert "Installed temporary trusted diffusion image catalog entry." in result.stdout
    assert "Restored the cluster diffusion image catalog." in result.stdout
    assert "cleanup_rc=0" in result.stdout
    assert (tmp_path / "kubectl-state").read_text(encoding="utf-8").strip() == (
        '{"llamacpp":{"default":"registry.example/llamacpp:cpu"}}'
    )
    record = (tmp_path / "kubectl-record").read_text(encoding="utf-8")
    assert record.count("patch configmap core-config") == 2
    assert record.count("rollout restart deployment/core-scheduler") == 2
    assert record.count("rollout status deployment/core-scheduler") == 2
