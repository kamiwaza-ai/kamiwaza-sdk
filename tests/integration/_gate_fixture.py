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

Serving the index over ``file://`` from inside the pod keeps the whole thing
independent of cluster topology: no Service, no node IP, no egress path, and
nothing for a future NetworkPolicy to break.

It writes the wheel/index into the gate-packages PVC and the dataset into the
Ray adapter's always-mounted ``/app/tmp`` allowed root. The temporary root is
present even on clusters without the optional fixture-model PVC, so the
federation suite can use a receiver-only package fixture without requiring a
second chart overlay. Both volumes are already mounted and writable, so the
fixture deliberately avoids a chart change:
mounting a ConfigMap would mean setting ``scheduler.extraVolumes``, and helm
REPLACES list values rather than merging them. No new volume or pod restart is
needed.

Usage::

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
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

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
# install path is independent of cluster routing.  NetworkPolicy validation
# needs an actual HTTP request from a worker, however, so the provisioner also
# serves that same index from the Ray head on this high, non-privileged port.
NETWORK_INDEX_PORT = 18080
NETWORK_INDEX_PIDFILE = "/tmp/kamiwaza-gate-index.pid"
NETWORK_INDEX_LOG = "/tmp/kamiwaza-gate-index.log"
DATASET_DIR = "/app/tmp"
DATASET_PATH = f"{DATASET_DIR}/eng10050-mini-clearance.csv"

REPO = Path(__file__).resolve().parents[2]
STAGE = REPO / ".gate-fixture"
WHEEL_DIR = STAGE / "wheels"
STAGE_DIR = STAGE / "index"


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
    sys.path.insert(0, str(Path(__file__).parent))
    import _mini_clearance as mc  # noqa: PLC0415 — sibling test helper

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


def _ray_head_pod(argv: list[str]) -> str:
    """Return the active ray-head pod name or stop with a useful error."""
    pod = run(
        argv
        + [
            "-n",
            NAMESPACE,
            "get",
            "pod",
            "-l",
            "ray.io/node-type=head",
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ]
    )
    name = pod.stdout.strip()
    if not name:
        raise SystemExit("no ray head pod found")
    return name


def _publish_item(argv: list[str], pod: str, item: Path, leaf: str) -> None:
    """Stream one staged file into its destination inside the ray head."""
    destination = (
        DATASET_PATH if item.name == "mini_clearance.csv" else f"{leaf}/{item.name}"
    )
    payload = base64.b64encode(item.read_bytes())
    cmd = argv + [
        "-n",
        NAMESPACE,
        "exec",
        "-i",
        pod,
        "-c",
        "ray-head",
        "--",
        "sh",
        "-c",
        f"base64 -d > {destination}",
    ]
    wrote = subprocess.run(
        _quote_remote_command(cmd),
        input=payload,
        capture_output=True,
        timeout=180,
    )
    if wrote.returncode != 0:
        detail = wrote.stderr.decode(errors="replace").strip()
        raise SystemExit(f"writing {item.name} failed: {detail}")


def _start_network_index(argv: list[str], pod: str) -> str:
    """Serve the staged PEP-503 index from the head for worker probes.

    The lifecycle install intentionally uses a ``file://`` URL.  Serving the
    identical bytes over a pod-local HTTP listener gives the worker
    NetworkPolicy probe a real network path without adding a chart service or
    making the product image depend on an external mirror.
    """
    script = (
        "set -eu; "
        "python_bin=$(command -v python3 || command -v python || true); "
        'test -n "$python_bin"; '
        f"if test -s {NETWORK_INDEX_PIDFILE}; then "
        f"  kill $(cat {NETWORK_INDEX_PIDFILE}) 2>/dev/null || true; "
        "fi; "
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
    ip = run(
        argv
        + [
            "-n",
            NAMESPACE,
            "get",
            "pod",
            pod,
            "-o",
            "jsonpath={.status.podIP}",
        ]
    )
    address = ip.stdout.strip()
    if ip.returncode != 0 or not address:
        raise SystemExit(
            "could not determine the Ray head pod IP for NetworkPolicy "
            f"validation: {ip.stderr.strip()}"
        )
    return f"http://{address}:{NETWORK_INDEX_PORT}/simple/acme-gates/"


def publish(argv: list[str], directory: Path) -> str:
    """Copy the staged wheel/index and dataset into existing Ray-head mounts.

    ``kubectl cp`` rather than a ConfigMap: no new volume means no chart change,
    no pod restart, and no risk of an extraVolumes overlay replacing the
    hot-reload source mounts.
    """
    name = _ray_head_pod(argv)

    leaf = f"{MOUNT}/simple/acme-gates"
    mk = run(
        argv
        + [
            "-n",
            NAMESPACE,
            "exec",
            name,
            "-c",
            "ray-head",
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
        _publish_item(argv, name, item, leaf)
    print(f"  published {len(items)} files into {MOUNT}")
    network_index_url = _start_network_index(argv, name)
    print(f"  serving NetworkPolicy probe index at {network_index_url}")
    return network_index_url


def verify(
    argv: list[str],
    digest: str,
    additional_digests: dict[str, str] | None = None,
) -> None:
    """The gate: the pod must serve the SAME bytes we hashed locally."""
    name = _ray_head_pod(argv)
    expected = {WHEEL_NAME: digest}
    expected.update(additional_digests or {})
    for wheel_name, wheel_digest in expected.items():
        remote = run(
            argv
            + [
                "-n",
                NAMESPACE,
                "exec",
                name,
                "-c",
                "ray-head",
                "--",
                "sha256sum",
                f"{MOUNT}/simple/acme-gates/{wheel_name}",
            ]
        )
        got = (remote.stdout.split() or [""])[0]
        want = wheel_digest.split(":", 1)[1]
        if got != want:
            raise SystemExit(
                "the wheel in the pod does not match the one hashed locally — the "
                f"install would fail its hash check.\n  wheel: {wheel_name}\n"
                f"  local: {want}\n  pod:   {got or remote.stderr.strip()}"
            )
        print(f"  verified in-pod wheel digest matches {wheel_name}: {want[:16]}…")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["provision", "teardown", "env"])
    ap.add_argument("--kubectl", default="kubectl")
    args = ap.parse_args()
    argv = kubectl_argv(args.kubectl)

    if args.action == "teardown":
        pod = run(
            argv
            + [
                "-n",
                NAMESPACE,
                "get",
                "pod",
                "-l",
                "ray.io/node-type=head",
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ]
        ).stdout.strip()
        if pod:
            run(
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
                    f"if test -s {NETWORK_INDEX_PIDFILE}; then "
                    f"kill $(cat {NETWORK_INDEX_PIDFILE}) 2>/dev/null || true; "
                    f"rm -f {NETWORK_INDEX_PIDFILE} {NETWORK_INDEX_LOG}; fi",
                ]
            )
            run(
                argv
                + [
                    "-n",
                    NAMESPACE,
                    "exec",
                    pod,
                    "-c",
                    "ray-head",
                    "--",
                    "rm",
                    "-rf",
                    MOUNT,
                    DATASET_PATH,
                ]
            )
        print(f"  removed {MOUNT} and {DATASET_PATH}")
        return 0

    wheels = build_wheels(locate_source())
    wheel, digest = wheels["1.1.0"]
    if args.action == "env":
        print(f"export M5_TEST_WHEEL_DIR={WHEEL_DIR}")
        print(f"export M5_TEST_INDEX_URL={INDEX_URL}")
        print(f"export MINI_CLEARANCE_DATASET_PATH={DATASET_PATH}")
        return 0

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
    print(f"\nexport M5_TEST_WHEEL_DIR={WHEEL_DIR}")
    print(f"export M5_TEST_INDEX_URL={INDEX_URL}")
    print(f"export M5_TEST_NETWORK_POLICY_ALLOWED_URL={network_index_url}")
    print(f"export MINI_CLEARANCE_DATASET_PATH={DATASET_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
