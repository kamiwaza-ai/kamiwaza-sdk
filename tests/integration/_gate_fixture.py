"""Provision the cluster-side rig the gated-retrieval live tests need (ENG-8325).

``test_federation_shared_idp_gated_retrieval_live.py`` skips unless
``mc.wheel_and_index()`` returns a value, which needs ``M5_TEST_WHEEL_DIR``,
``M5_TEST_INDEX_URL``, and the acme-gates wheel on disk. Without them the suite
reports 4 skipped / 0 passed and RC=0 — a permanently unexercised path that
reads like a pass.

The two env vars are consumed asymmetrically, and that asymmetry drives the
whole design:

* ``M5_TEST_WHEEL_DIR`` never leaves this machine. It exists so
  ``_wheel_sha256`` can compute the ``hash_digest`` sent to the server.
* ``M5_TEST_INDEX_URL`` is handed TO the cluster and dereferenced by pip inside
  the ray head.

So the digest is computed HERE and enforced THERE. The two must be the same
bytes or the install fails its hash check. Rather than rely on a reproducible
build producing an identical wheel twice, this builds ONCE and ships that exact
file into the ConfigMap — identical by construction.

Serving the index over ``file://`` from inside the pod keeps package installs
independent of cluster topology. The NetworkPolicy probe uses the chart-owned
Ray head Service, which gives Istio a mesh-routed destination instead of a raw
PodIP that cannot negotiate STRICT mTLS.

It writes the wheel/index into the gate-packages PVC and the dataset into the
Ray adapter's always-mounted ``/app/tmp`` allowed root. The temporary root is
present even on clusters without the optional fixture-model PVC, so the
federation suite can use a receiver-only package fixture without requiring a
second chart overlay. Both volumes are already mounted and writable, so the
fixture needs no new volume or pod restart. The opt-in smoke profile exposes
the existing Ray head Service on the fixture port so the probe remains inside
the mesh. Mounting a ConfigMap would mean setting ``scheduler.extraVolumes``,
and helm REPLACES list values rather than merging them, so the fixture keeps
using the existing mounts.

When ``M5_TEST_KUBECTL`` is set, the integration session provisions this rig
automatically after cluster convergence. Manual operation remains available::

    python -m tests.integration._gate_fixture provision [--kubectl "ssh spark-2 kubectl"]
    python -m tests.integration._gate_fixture env       [--kubectl ...]
    python -m tests.integration._gate_fixture teardown  [--kubectl ...]

``provision`` also prints ``M5_TEST_NETWORK_POLICY_ALLOWED_URL``.  Export that
value and set ``M5_TEST_NETWORK_POLICY_REQUIRED=1`` when running the required
gate-package NetworkPolicy cells.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The SDK owns both live fixture families.  The M5 lifecycle test exercises
# install 1.0.0 -> replace 1.0.1, while the federation known-answer tests use
# the fail-closed MiniClearanceGate in 1.1.0.  One provision command publishes
# all three exact artifacts so no package test needs to skip for missing
# fixture wheels.
PACKAGE_VERSIONS = ("1.0.0", "1.0.1", "1.1.0")
WHEEL_NAMES = {
    version: f"acme_gates-{version}-py3-none-any.whl" for version in PACKAGE_VERSIONS
}
WHEEL_NAME = WHEEL_NAMES["1.1.0"]
NAMESPACE = "kamiwaza"
# The gate-packages PVC: already mounted, already writable, and its presence IS
# the prerequisite for gate-package install.
MOUNT = "/opt/kamiwaza/gate-packages-venv/_fixture"
INDEX_URL = f"file://{MOUNT}/simple"
# The package lifecycle uses the receiver-local ``file://`` index so the
# install path is independent of cluster routing. NetworkPolicy validation
# needs an actual HTTP request from a worker, however, so the provisioner also
# serves that same index from the Ray head on this high, non-privileged port.
NETWORK_INDEX_PORT = 18080
NETWORK_INDEX_HOST = f"core-raycluster-head-svc.{NAMESPACE}.svc.cluster.local"
NETWORK_INDEX_PIDFILE = "/tmp/kamiwaza-gate-index.pid"
NETWORK_INDEX_LOG = "/tmp/kamiwaza-gate-index.log"
DATASET_DIR = "/app/tmp"
DATASET_PATH = f"{DATASET_DIR}/eng10050-mini-clearance.csv"
PUBLISH_ATTEMPTS = 3
PUBLISH_DIGEST_MISMATCH_RC = 74
# Set by workflows that explicitly provision before invoking pytest.  Manual
# callers leave this unset and retain the convenient automatic provisioning.
PREPROVISIONED_ENV = "M5_TEST_FIXTURE_PREPROVISIONED"

REPO = Path(__file__).resolve().parents[2]
STAGE = REPO / ".gate-fixture"
WHEEL_DIR = STAGE / "wheels"
STAGE_DIR = STAGE / "index"


@dataclass(frozen=True)
class RayPodTarget:
    """One active Ray pod and the workload container that owns its fixtures."""

    pod: str
    container: str


def _quote_remote_command(cmd: list[str]) -> list[str]:
    """Collapse an SSH remote command into the single argument its shell expects."""
    if len(cmd) <= 2:
        return cmd
    if cmd[0] != "ssh":
        return cmd
    return cmd[:2] + [shlex.join(cmd[2:])]


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run ``cmd``, shell-quoting the remote half when it goes over SSH.

    ``ssh host kubectl ... 'jsonpath={.items[0]...}'`` is re-parsed by the
    REMOTE shell, which globs the brackets and fails with "no matches found".
    Collapsing everything after the host into one quoted string stops that.
    """
    return subprocess.run(
        _quote_remote_command(cmd),
        capture_output=True,
        text=True,
        timeout=180,
        **kw,
    )


def kubectl_argv(kubectl: str) -> list[str]:
    """``kubectl``, ``kubectl --context=x`` and ``ssh spark-2 kubectl`` all work."""
    return shlex.split(kubectl)


def locate_source() -> Path:
    """Return the SDK-owned gate fixture source used for live validation."""
    candidate = REPO / "tests" / "integration" / "fixtures" / "acme-gates"
    if not (candidate / "pyproject.toml").exists():
        raise SystemExit(f"SDK gate fixture source not found at {candidate}")
    return candidate


def _build_error(cmd: list[str], staged: Path) -> str | None:
    """Run one wheel-build command and return its diagnostic on failure."""
    if shutil.which(cmd[0]) is None and cmd[0] != sys.executable:
        return f"{cmd[0]}: not on PATH"
    proc = run(cmd, cwd=staged)
    if proc.returncode == 0:
        return None
    return f"{cmd[0]}: {proc.stderr.strip()[:200]}"


def _build_first_available(staged: Path, output_dir: Path | None = None) -> None:
    """Try the uv and pip wheel builders in order."""
    destination = output_dir or WHEEL_DIR
    attempts = [
        ["uv", "build", "--wheel", "--out-dir", str(destination)],
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "-w",
            str(destination),
        ],
    ]
    errors = []
    for cmd in attempts:
        error = _build_error(cmd, staged)
        if error is None:
            return
        errors.append(error)
    raise SystemExit("wheel build failed:\n  " + "\n  ".join(errors))


def _stage_source(src: Path, version: str, stage_name: str) -> Path:
    """Copy the SDK fixture out of tree and set the wheel's package version."""
    staged = STAGE / stage_name
    if staged.exists():
        shutil.rmtree(staged)
    shutil.copytree(
        src,
        staged,
        ignore=shutil.ignore_patterns("__pycache__", "build", "dist", "*.egg-info"),
    )
    metadata = staged / "pyproject.toml"
    text = metadata.read_text(encoding="utf-8")
    rewritten, count = re.subn(
        r'(?m)^(version\s*=\s*)"[^"]+"',
        rf'\g<1>"{version}"',
        text,
        count=1,
    )
    if count != 1 and version != "1.1.0":
        raise SystemExit(f"could not set acme-gates version {version} in {metadata}")
    if count == 1:
        metadata.write_text(rewritten, encoding="utf-8")
    return staged


def _build_version(src: Path, version: str, stage_name: str) -> tuple[Path, str]:
    """Build one exact fixture version and return its path and SHA-256."""
    wheel_name = WHEEL_NAMES.get(version)
    if wheel_name is None:
        raise ValueError(f"unsupported acme-gates fixture version: {version}")
    staged = _stage_source(src, version, stage_name)
    _build_first_available(staged)
    wheel = WHEEL_DIR / wheel_name
    if not wheel.exists():
        built = sorted(p.name for p in WHEEL_DIR.glob("*.whl"))
        raise SystemExit(f"expected {wheel_name}, built {built}")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    return wheel, f"sha256:{digest}"


def build_wheel(src: Path, version: str = "1.1.0") -> tuple[Path, str]:
    """Build out-of-tree and return (wheel_path, 'sha256:<hex>').

    Staged out of tree because an in-tree build leaves build/ and *.egg-info in
    the kamiwaza repo, which is exactly the untracked residue this fixture
    should not create.
    """
    WHEEL_DIR.mkdir(parents=True, exist_ok=True)
    for existing in WHEEL_DIR.glob("*.whl"):
        existing.unlink()

    # uv first: the SDK venv is uv-managed and ships without pip, so
    # `python -m pip` is not available there. Fall back for environments that
    # have pip but not uv.
    return _build_version(src, version, "src")


def build_wheels(src: Path) -> dict[str, tuple[Path, str]]:
    """Build every SDK-owned wheel needed by the live suites."""
    WHEEL_DIR.mkdir(parents=True, exist_ok=True)
    for existing in WHEEL_DIR.glob("*.whl"):
        existing.unlink()
    return {
        version: _build_version(src, version, f"src-{version}")
        for version in PACKAGE_VERSIONS
    }


def stage_index(
    wheel: Path,
    digest: str,
    additional_wheels: dict[str, tuple[Path, str]] | None = None,
) -> Path:
    """Lay out the wheel, a PEP-503 leaf index, and the dataset.

    pip needs only the LEAF ``simple/<project>/index.html`` for an
    ``--index-url``; there is no root index to maintain.
    """
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    STAGE_DIR.mkdir(parents=True)
    artifacts = [(wheel, digest)] + list((additional_wheels or {}).values())
    anchors: list[str] = []
    for artifact, artifact_digest in artifacts:
        name = artifact.name
        shutil.copy2(artifact, STAGE_DIR / name)
        anchors.append(
            f'<a href="{name}#{artifact_digest.replace(":", "=")}">{name}</a>'
        )
    (STAGE_DIR / "index.html").write_text(
        "<!DOCTYPE html><html><body>" + "\n".join(sorted(anchors)) + "</body></html>\n",
        encoding="utf-8",
    )
    from tests.integration import _mini_clearance as mc  # noqa: PLC0415

    mc.write_dataset_file(STAGE_DIR / "mini_clearance.csv")
    return STAGE_DIR


def preflight(argv: list[str]) -> None:
    """Fail with the remediation command rather than an obscure pytest error."""
    probe = run(
        argv
        + [
            "-n",
            NAMESPACE,
            "get",
            "pod",
            "-l",
            "ray.io/node-type=head",
            "-o",
            'jsonpath={.items[0].spec.containers[0].env[?(@.name=="KAMIWAZA_GATE_PACKAGES_VENV")].value}',
        ]
    )
    if probe.returncode != 0:
        raise SystemExit(f"kubectl unreachable: {probe.stderr.strip()}")
    if not probe.stdout.strip():
        raise SystemExit(
            "the ray head has no KAMIWAZA_GATE_PACKAGES_VENV, so the gate-packages "
            "PVC is not enabled and install would fail fast at the API layer.\n"
            "Remediate: set core.authz.gatePackages.pvc.enabled=true in\n"
            "cluster/values/overrides.yaml, then DOMAIN=<fqdn> make dev-full"
        )


def _target_from_pod(pod: dict[str, Any]) -> RayPodTarget | None:
    """Convert one Kubernetes pod document into a supported Ray target."""
    metadata = pod.get("metadata", {})
    if metadata.get("deletionTimestamp"):
        return None
    node_type = metadata.get("labels", {}).get("ray.io/node-type")
    if node_type not in {"head", "worker"}:
        return None
    name = metadata.get("name")
    if not name:
        return None
    return RayPodTarget(pod=name, container=f"ray-{node_type}")


def _head_target(targets: tuple[RayPodTarget, ...]) -> RayPodTarget:
    """Return the active Ray head from an already consistent target snapshot."""
    for target in targets:
        if target.container == "ray-head":
            return target
    raise SystemExit("no running ray head pod found")


def _ray_targets(argv: list[str]) -> tuple[RayPodTarget, ...]:
    """Return every non-terminating, running Ray head and worker pod."""
    pods = run(
        argv
        + [
            "-n",
            NAMESPACE,
            "get",
            "pods",
            "-l",
            "ray.io/cluster=core-raycluster",
            "--field-selector",
            "status.phase=Running",
            "-o",
            "json",
        ]
    )
    if pods.returncode != 0:
        raise SystemExit(f"could not list Ray pods: {pods.stderr.strip()}")
    try:
        payload = json.loads(pods.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"could not parse Ray pod list: {exc}") from exc
    targets = []
    for pod in payload.get("items", []):
        target = _target_from_pod(pod)
        if target is not None:
            targets.append(target)
    result = tuple(sorted(targets, key=lambda target: (target.container, target.pod)))
    _head_target(result)
    return result


def _publish_script(destination: str, expected_digest: str) -> str:
    """Decode to a temporary file and atomically publish exact bytes."""
    temporary = shlex.quote(f"{destination}.partial")
    target = shlex.quote(destination)
    digest = shlex.quote(expected_digest)
    return (
        "set -eu; "
        f"trap 'rm -f {temporary}' 0; "
        f"base64 -d > {temporary}; "
        f"set -- $(sha256sum {temporary}); "
        f'if [ "$1" != {digest} ]; then '
        f"echo 'published digest mismatch' >&2; exit {PUBLISH_DIGEST_MISMATCH_RC}; "
        "fi; "
        f"mv {temporary} {target}; trap - 0"
    )


def _publish_item(argv: list[str], target: RayPodTarget, item: Path, leaf: str) -> None:
    """Stream one staged file into its destination inside one Ray pod."""
    destination = (
        DATASET_PATH if item.name == "mini_clearance.csv" else f"{leaf}/{item.name}"
    )
    raw = item.read_bytes()
    payload = base64.b64encode(raw)
    expected_digest = hashlib.sha256(raw).hexdigest()
    cmd = argv + [
        "-n",
        NAMESPACE,
        "exec",
        "-i",
        target.pod,
        "-c",
        target.container,
        "--",
        "sh",
        "-c",
        _publish_script(destination, expected_digest),
    ]
    detail = ""
    for _attempt in range(PUBLISH_ATTEMPTS):
        wrote = subprocess.run(
            _quote_remote_command(cmd),
            input=payload,
            capture_output=True,
            timeout=180,
        )
        if wrote.returncode == 0:
            return
        detail = wrote.stderr.decode(errors="replace").strip()
        if wrote.returncode != PUBLISH_DIGEST_MISMATCH_RC:
            break
    raise SystemExit(f"writing {item.name} failed: {detail}")


def _stop_network_index_script(*, remove_log: bool = False) -> str:
    """Stop only the fixture-owned HTTP server recorded in the PID file."""
    cleanup = (
        f"rm -f {NETWORK_INDEX_PIDFILE} {NETWORK_INDEX_LOG}"
        if remove_log
        else f"rm -f {NETWORK_INDEX_PIDFILE}"
    )
    return (
        f"if test -s {NETWORK_INDEX_PIDFILE}; then "
        f"  pid=$(cat {NETWORK_INDEX_PIDFILE} 2>/dev/null || true); "
        '  case "$pid" in '
        "    ''|*[!0-9]*) ;; "
        "    *) "
        "      cmdline=$(tr '\\0' ' ' < /proc/$pid/cmdline 2>/dev/null || true); "
        f"      case \"$cmdline\" in *' -m http.server {NETWORK_INDEX_PORT} '*|*' -m http.server {NETWORK_INDEX_PORT}') "
        '        kill "$pid" 2>/dev/null || true ;; '
        "      esac ;; "
        "  esac; "
        f"  {cleanup}; "
        "fi;"
    )


def _start_network_index(argv: list[str], pod: str) -> str:
    """Serve the staged PEP-503 index from the head for worker probes.

    The lifecycle install intentionally uses a ``file://`` URL.  Serving the
    identical bytes over an HTTP listener gives the worker NetworkPolicy probe
    a real network path. The chart exposes this opt-in port on the existing
    mesh-routed Ray head Service; a raw PodIP would bypass Istio auto-mTLS.
    """
    script = (
        "set -eu; "
        + _stop_network_index_script()
        + "python_bin=$(command -v python3 || command -v python || true); "
        'test -n "$python_bin"; '
        f'nohup "$python_bin" -m http.server {NETWORK_INDEX_PORT} '
        f"--bind 0.0.0.0 --directory {MOUNT} "
        f">{NETWORK_INDEX_LOG} 2>&1 </dev/null & echo $! > {NETWORK_INDEX_PIDFILE}; "
        "sleep 1; "
        f"kill -0 $(cat {NETWORK_INDEX_PIDFILE});"
    )
    started = run(
        argv
        + [
            "-n",
            NAMESPACE,
            "exec",
            pod,
            "-c",
            "ray-head",
            "--",
            "sh",
            "-c",
            script,
        ]
    )
    if started.returncode != 0:
        raise SystemExit(
            f"could not start the in-pod package index server: {started.stderr.strip()}"
        )
    return f"http://{NETWORK_INDEX_HOST}:{NETWORK_INDEX_PORT}/simple/acme-gates/"


def publish(argv: list[str], directory: Path) -> str:
    """Publish head-only packages and mirror the dataset into every Ray pod.

    ``kubectl cp`` rather than a ConfigMap: no new volume or pod restart, and no
    risk of an extraVolumes overlay replacing the hot-reload source mounts.
    """
    targets = _ray_targets(argv)
    head = _head_target(targets)

    leaf = f"{MOUNT}/simple/acme-gates"
    mk = run(
        argv
        + [
            "-n",
            NAMESPACE,
            "exec",
            head.pod,
            "-c",
            head.container,
            "--",
            "mkdir",
            "-p",
            leaf,
            DATASET_DIR,
        ]
    )
    if mk.returncode != 0:
        raise SystemExit(f"could not create {leaf}: {mk.stderr.strip()}")

    # Stream each file in over stdin rather than `kubectl cp`. When kubectl is
    # reached over ssh, cp's "local" side is the REMOTE host, so it looks for
    # the file there. stdin comes from this process either way. base64 so the
    # binary wheel survives the ssh channel intact.
    items = sorted(directory.iterdir())
    for item in items:
        destinations = targets if item.name == "mini_clearance.csv" else (head,)
        for target in destinations:
            _publish_item(argv, target, item, leaf)
    print(f"  published {len(items)} staged files across {len(targets)} Ray pods")
    network_index_url = _start_network_index(argv, head.pod)
    print(f"  serving NetworkPolicy probe index at {network_index_url}")
    return network_index_url


def _verify_remote_digest(
    argv: list[str], target: RayPodTarget, path: str, expected: str
) -> None:
    """Require one pod path to match an exact local SHA-256 digest."""
    remote = run(
        argv
        + [
            "-n",
            NAMESPACE,
            "exec",
            target.pod,
            "-c",
            target.container,
            "--",
            "sha256sum",
            path,
        ]
    )
    got = (remote.stdout.split() or [""])[0]
    if got != expected:
        detail = got or remote.stderr.strip()
        raise SystemExit(
            f"published fixture digest mismatch: {path}\n"
            f"  pod:   {target.pod}\n  local: {expected}\n  pod:   {detail}"
        )


def verify(
    argv: list[str],
    digest: str,
    additional_digests: dict[str, str] | None = None,
    *,
    dataset: Path | None = None,
) -> None:
    """Require head package bytes and every Ray pod's dataset to match local."""
    targets = _ray_targets(argv)
    head = _head_target(targets)
    expected = {WHEEL_NAME: digest}
    expected.update(additional_digests or {})
    for wheel_name, wheel_digest in expected.items():
        want = wheel_digest.split(":", 1)[1]
        _verify_remote_digest(
            argv,
            head,
            f"{MOUNT}/simple/acme-gates/{wheel_name}",
            want,
        )
        print(f"  verified in-pod wheel digest matches {wheel_name}: {want[:16]}…")
    dataset_path = dataset or STAGE_DIR / "mini_clearance.csv"
    dataset_digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    for target in targets:
        _verify_remote_digest(argv, target, DATASET_PATH, dataset_digest)
        print(
            "  verified in-pod dataset digest matches "
            f"{target.pod}: {dataset_digest[:16]}…"
        )


def _fixture_environment(network_index_url: str | None = None) -> dict[str, str]:
    """Return the environment consumed by the package and federation suites."""
    values = {
        "M5_TEST_WHEEL_DIR": str(WHEEL_DIR),
        "M5_TEST_INDEX_URL": INDEX_URL,
        "MINI_CLEARANCE_DATASET_PATH": DATASET_PATH,
    }
    if network_index_url:
        values["M5_TEST_NETWORK_POLICY_ALLOWED_URL"] = network_index_url
    return values


def _print_environment(values: dict[str, str]) -> None:
    """Print shell exports for manual fixture operation."""
    for name, value in values.items():
        print(f"export {name}={value}")


def provision(argv: list[str]) -> dict[str, str]:
    """Refresh cluster-side fixtures and return their pytest environment."""
    wheels = build_wheels(locate_source())
    wheel, digest = wheels["1.1.0"]
    for version, (built_wheel, built_digest) in wheels.items():
        print(f"  built {version}: {built_wheel.name}  {built_digest}")

    preflight(argv)
    additional = {
        version: artifact for version, artifact in wheels.items() if version != "1.1.0"
    }
    network_index_url = publish(argv, stage_index(wheel, digest, additional))
    verify(
        argv,
        digest,
        {artifact[0].name: artifact[1] for artifact in additional.values()},
    )
    return _fixture_environment(network_index_url)


def auto_provision_from_env() -> dict[str, str]:
    """Provision only when an explicit kubectl command selects a test cluster."""
    if os.getenv(PREPROVISIONED_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return {}
    kubectl = os.getenv("M5_TEST_KUBECTL", "").strip()
    if not kubectl:
        return {}
    return provision(kubectl_argv(kubectl))


def teardown(argv: list[str]) -> None:
    """Remove only runtime state owned by this fixture."""
    targets = _ray_targets(argv)
    head = _head_target(targets)
    run(
        argv
        + [
            "-n",
            NAMESPACE,
            "exec",
            head.pod,
            "-c",
            head.container,
            "--",
            "sh",
            "-c",
            _stop_network_index_script(remove_log=True),
        ]
    )
    run(
        argv
        + [
            "-n",
            NAMESPACE,
            "exec",
            head.pod,
            "-c",
            head.container,
            "--",
            "rm",
            "-rf",
            MOUNT,
        ]
    )
    for target in targets:
        run(
            argv
            + [
                "-n",
                NAMESPACE,
                "exec",
                target.pod,
                "-c",
                target.container,
                "--",
                "rm",
                "-f",
                DATASET_PATH,
            ]
        )
    print(f"  removed {MOUNT} and {DATASET_PATH} from {len(targets)} Ray pods")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["provision", "teardown", "env"])
    ap.add_argument("--kubectl", default="kubectl")
    args = ap.parse_args()
    argv = kubectl_argv(args.kubectl)

    if args.action == "teardown":
        teardown(argv)
        return 0

    if args.action == "env":
        build_wheels(locate_source())
        _print_environment(_fixture_environment())
        return 0

    values = provision(argv)
    print()
    _print_environment(values)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
