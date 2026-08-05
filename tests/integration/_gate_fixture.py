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

It writes into the gate-packages PVC, which is already mounted and already
writable (``KAMIWAZA_GATE_PACKAGES_VENV``) whenever gate-packages are enabled at
all — the exact prerequisite this rig needs anyway. That deliberately avoids a
chart change: mounting a ConfigMap would mean setting ``scheduler.extraVolumes``,
and helm REPLACES list values rather than merging them, so an overlay composed
after ``kamiwaza-hotreload-k0s.yaml`` would silently clobber the source mounts
and break hot-reload. No new volume, no pod restart, no hazard.

Usage::

    python -m tests.integration._gate_fixture provision [--kubectl "ssh spark-2 kubectl"]
    python -m tests.integration._gate_fixture env       [--kubectl ...]
    python -m tests.integration._gate_fixture teardown  [--kubectl ...]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

WHEEL_NAME = "acme_gates-1.1.0-py3-none-any.whl"
NAMESPACE = "kamiwaza"
# The gate-packages PVC: already mounted, already writable, and its presence IS
# the prerequisite for gate-package install.
MOUNT = "/opt/kamiwaza/gate-packages-venv/_fixture"
INDEX_URL = f"file://{MOUNT}/simple"

REPO = Path(__file__).resolve().parents[2]
STAGE = REPO / ".gate-fixture"
WHEEL_DIR = STAGE / "wheels"
STAGE_DIR = STAGE / "index"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run ``cmd``, shell-quoting the remote half when it goes over ssh.

    ``ssh host kubectl ... 'jsonpath={.items[0]...}'`` is re-parsed by the
    REMOTE shell, which globs the brackets and fails with "no matches found".
    Collapsing everything after the host into one quoted string stops that.
    """
    if cmd and cmd[0] == "ssh" and len(cmd) > 2:
        cmd = cmd[:2] + [shlex.join(cmd[2:])]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180, **kw)


def kubectl_argv(kubectl: str) -> list[str]:
    """``kubectl``, ``kubectl --context=x`` and ``ssh spark-2 kubectl`` all work."""
    return shlex.split(kubectl)


def locate_source() -> Path:
    """The acme-gates fixture package lives in the kamiwaza repo, not here."""
    candidate = REPO.parent / "kamiwaza" / "tests" / "fixtures" / "acme-gates"
    if not (candidate / "pyproject.toml").exists():
        raise SystemExit(
            f"acme-gates source not found at {candidate}. It ships in the kamiwaza "
            "repo at tests/fixtures/acme-gates; clone it beside kamiwaza-sdk."
        )
    return candidate


def build_wheel(src: Path) -> tuple[Path, str]:
    """Build out-of-tree and return (wheel_path, 'sha256:<hex>').

    Staged out of tree because an in-tree build leaves build/ and *.egg-info in
    the kamiwaza repo, which is exactly the untracked residue this fixture
    should not create.
    """
    WHEEL_DIR.mkdir(parents=True, exist_ok=True)
    staged = STAGE / "src"
    if staged.exists():
        shutil.rmtree(staged)
    shutil.copytree(
        src, staged, ignore=shutil.ignore_patterns("__pycache__", "build", "dist", "*.egg-info")
    )
    for existing in WHEEL_DIR.glob("*.whl"):
        existing.unlink()

    # uv first: the SDK venv is uv-managed and ships without pip, so
    # `python -m pip` is not available there. Fall back for environments that
    # have pip but not uv.
    attempts = [
        ["uv", "build", "--wheel", "--out-dir", str(WHEEL_DIR)],
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(WHEEL_DIR)],
    ]
    errors = []
    for cmd in attempts:
        if shutil.which(cmd[0]) is None and cmd[0] != sys.executable:
            errors.append(f"{cmd[0]}: not on PATH")
            continue
        proc = run(cmd, cwd=staged)
        if proc.returncode == 0:
            break
        errors.append(f"{cmd[0]}: {proc.stderr.strip()[:200]}")
    else:
        raise SystemExit("wheel build failed:\n  " + "\n  ".join(errors))

    wheel = WHEEL_DIR / WHEEL_NAME
    if not wheel.exists():
        built = sorted(p.name for p in WHEEL_DIR.glob("*.whl"))
        raise SystemExit(f"expected {WHEEL_NAME}, built {built}")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    return wheel, f"sha256:{digest}"


def stage_index(wheel: Path, digest: str) -> Path:
    """Lay out the wheel, a PEP-503 leaf index, and the dataset.

    pip needs only the LEAF ``simple/<project>/index.html`` for an
    ``--index-url``; there is no root index to maintain.
    """
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    STAGE_DIR.mkdir(parents=True)
    shutil.copy2(wheel, STAGE_DIR / WHEEL_NAME)
    (STAGE_DIR / "index.html").write_text(
        "<!DOCTYPE html><html><body>"
        f'<a href="{WHEEL_NAME}#{digest.replace(":", "=")}">{WHEEL_NAME}</a>'
        "</body></html>\n",
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
            "-n", NAMESPACE, "get", "pod", "-l", "ray.io/node-type=head",
            "-o", 'jsonpath={.items[0].spec.containers[0].env[?(@.name=="KAMIWAZA_GATE_PACKAGES_VENV")].value}',
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


def publish(argv: list[str], directory: Path) -> None:
    """Copy the staged index into the PVC path the ray head already mounts.

    ``kubectl cp`` rather than a ConfigMap: no new volume means no chart change,
    no pod restart, and no risk of an extraVolumes overlay replacing the
    hot-reload source mounts.
    """
    pod = run(argv + ["-n", NAMESPACE, "get", "pod", "-l", "ray.io/node-type=head",
                      "-o", "jsonpath={.items[0].metadata.name}"])
    name = pod.stdout.strip()
    if not name:
        raise SystemExit("no ray head pod found")

    leaf = f"{MOUNT}/simple/acme-gates"
    mk = run(argv + ["-n", NAMESPACE, "exec", name, "-c", "ray-head", "--",
                     "mkdir", "-p", leaf])
    if mk.returncode != 0:
        raise SystemExit(f"could not create {leaf}: {mk.stderr.strip()}")

    # Stream each file in over stdin rather than `kubectl cp`. When kubectl is
    # reached over ssh, cp's "local" side is the REMOTE host, so it looks for
    # the file there. stdin comes from this process either way. base64 so the
    # binary wheel survives the ssh channel intact.
    for item in sorted(directory.iterdir()):
        dest_dir = MOUNT if item.name == "mini_clearance.csv" else leaf
        payload = base64.b64encode(item.read_bytes())
        cmd = argv + ["-n", NAMESPACE, "exec", "-i", name, "-c", "ray-head", "--",
                      "sh", "-c", f"base64 -d > {dest_dir}/{item.name}"]
        if cmd[0] == "ssh" and len(cmd) > 2:
            cmd = cmd[:2] + [shlex.join(cmd[2:])]
        wrote = subprocess.run(cmd, input=payload, capture_output=True, timeout=180)
        if wrote.returncode != 0:
            raise SystemExit(
                f"writing {item.name} failed: {wrote.stderr.decode(errors='replace').strip()}"
            )
    print(f"  published {len(list(directory.iterdir()))} files into {MOUNT}")


def verify(argv: list[str], digest: str) -> None:
    """The gate: the pod must serve the SAME bytes we hashed locally."""
    pod = run(argv + ["-n", NAMESPACE, "get", "pod", "-l", "ray.io/node-type=head",
                      "-o", "jsonpath={.items[0].metadata.name}"])
    name = pod.stdout.strip()
    if not name:
        raise SystemExit("no ray head pod found")
    remote = run(
        argv + ["-n", NAMESPACE, "exec", name, "-c", "ray-head", "--",
                "sha256sum", f"{MOUNT}/simple/acme-gates/{WHEEL_NAME}"]
    )
    got = (remote.stdout.split() or [""])[0]
    want = digest.split(":", 1)[1]
    if got != want:
        raise SystemExit(
            "the wheel in the pod does not match the one hashed locally — the "
            f"install would fail its hash check.\n  local: {want}\n  pod:   {got or remote.stderr.strip()}"
        )
    print(f"  verified in-pod wheel digest matches: {want[:16]}…")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["provision", "teardown", "env"])
    ap.add_argument("--kubectl", default="kubectl")
    args = ap.parse_args()
    argv = kubectl_argv(args.kubectl)

    if args.action == "teardown":
        pod = run(argv + ["-n", NAMESPACE, "get", "pod", "-l", "ray.io/node-type=head",
                          "-o", "jsonpath={.items[0].metadata.name}"]).stdout.strip()
        if pod:
            run(argv + ["-n", NAMESPACE, "exec", pod, "-c", "ray-head", "--",
                        "rm", "-rf", MOUNT])
        print(f"  removed {MOUNT}")
        return 0

    wheel, digest = build_wheel(locate_source())
    if args.action == "env":
        print(f"export M5_TEST_WHEEL_DIR={WHEEL_DIR}")
        print(f"export M5_TEST_INDEX_URL={INDEX_URL}")
        return 0

    print(f"  built {wheel.name}  {digest}")
    preflight(argv)
    publish(argv, stage_index(wheel, digest))
    verify(argv, digest)
    print(f"\nexport M5_TEST_WHEEL_DIR={WHEEL_DIR}")
    print(f"export M5_TEST_INDEX_URL={INDEX_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
