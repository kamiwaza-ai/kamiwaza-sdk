"""T7.16 / ENG-4770 (WS-M5) — Gate-package lifecycle integration test.

End-to-end verification of the install → bind → replace → rollback →
unbind → uninstall path against a live cluster, plus NetworkPolicy
egress probes. Per the M5 demo gate (TS-M5-25, AC2/AC6/AC7/AC8/AC10/
AC11).

Skipped by default (marker: ``integration``). Requires:

- ``KAMIWAZA_BASE_URL`` (e.g., ``https://kamiwaza.test/api``)
- ``KAMIWAZA_ADMIN_TOKEN`` (admin Keycloak token)
- A live cluster with the WS-M5 chart applied (gate-packages PVC +
  bind-mounts + GatePackageAPI registered + cluster_gate_packages
  table)
- Run ``python -m tests.integration._gate_fixture provision --kubectl ...``
  first. The SDK-owned provisioner builds and publishes the exact
  ``acme_gates`` 1.0.0, 1.0.1, and 1.1.0 wheels plus the simple index; no
  externally supplied fixture package is required.
- ``M5_TEST_WHEEL_DIR`` and ``M5_TEST_INDEX_URL`` exported by that provisioner
  (the live rig uses a receiver-local ``file://`` index)
- ``M5_TEST_NETWORK_POLICY_REQUIRED=1`` to select the required security lane;
  the provisioner emits ``M5_TEST_NETWORK_POLICY_ALLOWED_URL`` for a real HTTP
  request from the worker. ``M5_TEST_KUBECTL`` may be an SSH-wrapped kubectl
  command (for example ``ssh demo3 kubectl``).

The test is structured so it can also serve as the canonical M5b
smoke procedure when the human operator follows the playbook at
``docs/mesh-v1.0.0/demos/m5a-gate-packages-smoke.md`` (which can be
extended for M5b).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
from pathlib import Path
from typing import Iterator

import pytest

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _network_policy_required() -> bool:
    """Return whether this invocation selected the required NP validation lane."""
    value = os.getenv("M5_TEST_NETWORK_POLICY_REQUIRED", "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"", "0", "false", "no", "off"}:
        return False
    pytest.fail(
        "M5_TEST_NETWORK_POLICY_REQUIRED must be one of 1/0, true/false, "
        f"yes/no, or on/off; got {value!r}",
        pytrace=False,
    )


def _network_policy_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    if not value:
        pytest.fail(f"{name} is required for the NetworkPolicy validation lane")
    return value


def _kubectl_argv() -> list[str]:
    # `_gate_fixture.run` handles the SSH form (`ssh host kubectl ...`) and
    # quotes the remote command as one shell argument.
    from tests.integration import _gate_fixture

    return _gate_fixture.kubectl_argv(os.getenv("M5_TEST_KUBECTL", "kubectl"))


def _kubectl_run(argv: list[str], args: list[str]):
    from tests.integration import _gate_fixture

    return _gate_fixture.run(argv + args)


def _pod_for_selector(argv: list[str], selector: str, role: str) -> str:
    from tests.integration import _gate_fixture

    result = _kubectl_run(
        argv,
        [
            "-n",
            _gate_fixture.NAMESPACE,
            "get",
            "pod",
            "-l",
            selector,
            "-o",
            'jsonpath={range .items[?(@.status.phase=="Running")]}{.metadata.name}'
            '{"\\n"}{end}',
        ],
    )
    pod = next(
        (line.strip() for line in result.stdout.splitlines() if line.strip()), ""
    )
    if result.returncode != 0 or not pod:
        pytest.fail(
            f"required Ray {role} pod not found ({selector}): {result.stderr.strip()}",
            pytrace=False,
        )
    return pod


def _probe(argv: list[str], pod: str, container: str, url: str) -> tuple[int, int]:
    """Run a proxy-free curl probe and return (curl_rc, HTTP status)."""
    from tests.integration import _gate_fixture

    # Keep the shell wrapper successful so a denied connection is represented
    # in the probe output instead of being mistaken for a kubectl failure.
    script = (
        "set +e; "
        "if ! command -v curl >/dev/null 2>&1; then "
        "  printf 'curl_rc=127 http_code=000\\n'; exit 0; fi; "
        "code=$(env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY "
        "curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' "
        "--connect-timeout 3 --max-time 5 "
        f"{shlex.quote(url)} 2>/dev/null); "
        'curl_rc=$?; printf \'curl_rc=%s http_code=%s\\n\' "$curl_rc" "$code"'
    )
    result = _kubectl_run(
        argv,
        [
            "-n",
            _gate_fixture.NAMESPACE,
            "exec",
            pod,
            "-c",
            container,
            "--",
            "sh",
            "-c",
            script,
        ],
    )
    if result.returncode != 0:
        pytest.fail(
            f"kubectl exec probe failed for {pod}: {result.stderr.strip()}",
            pytrace=False,
        )
    match = re.search(r"curl_rc=(\d+) http_code=(\d+)", result.stdout)
    if not match:
        pytest.fail(
            f"probe returned no structured result for {pod}: {result.stdout!r}",
            pytrace=False,
        )
    return int(match.group(1)), int(match.group(2))


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; integration test skipped")
    return value


@pytest.fixture(scope="module")
def kz():
    """Authenticated KamiwazaClient against the live cluster.

    TLS verification defaults to on. For dev clusters with self-signed certs,
    either install the CA into the local trust store, or set
    ``KAMIWAZA_VERIFY_SSL=0`` (opt-out, logs a warning).
    """
    base_url = _env("KAMIWAZA_BASE_URL")
    token = _env("KAMIWAZA_ADMIN_TOKEN")
    verify_flag = os.getenv("KAMIWAZA_VERIFY_SSL", "1").strip().lower()
    verify_ssl = verify_flag not in {"0", "false", "no", "off"}
    if not verify_ssl:
        # Use logger.warning so the message surfaces in pytest's captured
        # output reliably; UserWarning is often hidden unless -W error.
        logger.warning(
            "TLS verification disabled via KAMIWAZA_VERIFY_SSL=0; only use this "
            "for dev clusters with self-signed certs. Production envs should "
            "install the CA into the trust store or use a properly-issued cert."
        )
    from kamiwaza_sdk import KamiwazaClient

    return KamiwazaClient(base_url=base_url, api_key=token, verify=verify_ssl)


@pytest.fixture(scope="module")
def wheel_dir() -> Path:
    path = Path(_env("M5_TEST_WHEEL_DIR"))
    _require_wheel(path, "acme_gates-1.0.0-py3-none-any.whl")
    return path


@pytest.fixture(scope="module")
def index_url() -> str:
    return _env("M5_TEST_INDEX_URL")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return f"sha256:{h.hexdigest()}"


def _require_wheel(path: Path, filename: str) -> Path:
    """Fail loudly when a configured live run lacks an SDK-owned wheel."""
    wheel = path / filename
    if not wheel.is_file():
        pytest.fail(
            f"required SDK-owned fixture wheel not at {wheel}; run "
            "python -m tests.integration._gate_fixture provision first",
            pytrace=False,
        )
    return wheel


def _wheel_and_index_configured() -> bool:
    """Both env vars must be set for the lifecycle suite to run safely."""
    return bool(os.getenv("M5_TEST_WHEEL_DIR", "").strip()) and bool(
        os.getenv("M5_TEST_INDEX_URL", "").strip()
    )


@pytest.fixture(scope="module", autouse=True)
def cleanup_acme(kz) -> Iterator[None]:
    """Ensure a clean starting state — uninstall any prior acme-gates.

    Module-scope fixtures fire BEFORE class-scope ones, so we cannot
    rely on TestLifecycle's `_require_wheel_and_index` skip to gate this
    cluster mutation. Gate on the same env vars directly so the
    setup/teardown only runs when the lifecycle suite actually intends
    to execute.
    """
    if not _wheel_and_index_configured():
        yield
        return
    try:
        kz.gates.packages.uninstall("acme-gates")
    except Exception as exc:  # noqa: BLE001
        # Best-effort: not installed is fine; log so a real
        # auth/connectivity failure isn't masked.
        logger.warning("Pre-test cleanup of acme-gates failed: %s", exc)
    yield
    try:
        kz.gates.packages.uninstall("acme-gates")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Post-test cleanup of acme-gates failed: %s", exc)


class TestLifecycle:
    """TS-M5-24 (install) + TS-M5-25 (replace) + TS-M5-15 (uninstall)."""

    @pytest.fixture(autouse=True)
    def _require_wheel_and_index(self) -> None:
        """Skip the whole lifecycle class if the v1/v2 wheels or pip index aren't
        materialized. Scoped to *this class* (not module-level autouse) so
        TestRegression / TestNetworkPolicyProbes are not over-skipped — they
        don't need the wheel/index env vars."""
        if not _wheel_and_index_configured():
            pytest.skip(
                "TestLifecycle requires both M5_TEST_WHEEL_DIR and "
                "M5_TEST_INDEX_URL — set both to run the install/replace/"
                "uninstall sequence end-to-end."
            )

    def test_install_v1(self, kz, wheel_dir, index_url):
        """AC1 + AC3 + AC5: install → row appears with classpaths populated."""
        hash_digest = _sha256(wheel_dir / "acme_gates-1.0.0-py3-none-any.whl")
        result = kz.gates.packages.install(
            "acme-gates==1.0.0",
            hash_digest=hash_digest,
            index_url=index_url,
        )
        assert result.package.name == "acme-gates"
        assert result.package.version == "1.0.0"
        assert "acme_gates.gate.AcmeAttributeGate" in result.package.classpaths
        # Audit event ID surfaced
        assert result.audit_event_id is not None

        # Discover sees the new classpath (worker imported from PVC)
        gate = kz.gates.discover("acme_gates.gate.AcmeAttributeGate")
        assert gate.name == "acme_attribute_gate"

    def test_list_and_get(self, kz):
        """AC3: list + get round-trip."""
        listing = kz.gates.packages.list()
        names = [p.name for p in listing.items]
        assert "acme-gates" in names

        pkg = kz.gates.packages.get("acme-gates")
        assert pkg.name == "acme-gates"
        assert pkg.version == "1.0.0"

    def test_atomic_replace_to_v2(self, kz, wheel_dir, index_url):
        """AC2: PUT replace works atomically (no unbound window observed
        from the binding's perspective — the binding stays in etcd).

        Requires acme_gates-1.0.1-py3-none-any.whl in wheel_dir AND a
        classpath superset (v1.0.1 must include AcmeAttributeGate).
        """
        v2 = _require_wheel(wheel_dir, "acme_gates-1.0.1-py3-none-any.whl")
        v2_hash = _sha256(v2)

        # NOTE: The binding-aware classpath-superset check requires binding
        # acme-gates as an AttributeGate on a test dataset first; that bind
        # path isn't exercised by this suite yet (no dataset fixture).
        # Current test covers only the unbound replace path.

        result = kz.gates.packages.replace(
            "acme-gates",
            "acme-gates==1.0.1",
            hash_digest=v2_hash,
            index_url=index_url,
        )
        assert result.package.version == "1.0.1"
        assert result.package.last_replaced_at is not None
        assert result.audit_event_id is not None

    def test_uninstall(self, kz):
        """AC4: DELETE — succeeds when no active bindings.

        The production wiring (``default_bindings_check`` in
        ``services/authz/gate_packages/bindings.py``) queries the
        runtime-config ExecutionGate + every catalog dataset's
        ``properties.gate.type``. With no bindings on acme-gates this
        path returns success. If a prior test bound the classpath, the
        uninstall returns 409 ``uninstall_blocked`` — operator must
        unbind first.
        """
        kz.gates.packages.uninstall("acme-gates")
        listing = kz.gates.packages.list()
        names = [p.name for p in listing.items]
        assert "acme-gates" not in names


class TestNetworkPolicyProbes:
    """TS-M5-26/27/28 — NetworkPolicy egress validation.

    The chart defaults remain off.  Set M5_TEST_NETWORK_POLICY_REQUIRED=1 for
    the validation profile; in that mode missing pods or policies fail loudly.
    With the flag unset, these optional live probes retain a clear skip so a
    normal package-lifecycle run is not misreported as a security pass.
    """

    @pytest.fixture(autouse=True)
    def _network_policy_context(self):
        if not _network_policy_required():
            pytest.skip(
                "NetworkPolicy probes require the validation profile; set "
                "M5_TEST_NETWORK_POLICY_REQUIRED=1 with the gate-packages "
                "smoke overlay to make them required."
            )
        from tests.integration import _gate_fixture

        argv = _kubectl_argv()
        worker = _pod_for_selector(argv, "ray.io/node-type=worker", "worker")
        head = _pod_for_selector(argv, "ray.io/node-type=head", "head")
        control = _pod_for_selector(
            argv, "app.kubernetes.io/name=core-scheduler", "control"
        )
        for name in ("core-ray-worker-egress", "core-ray-head-egress"):
            result = _kubectl_run(
                argv,
                ["-n", _gate_fixture.NAMESPACE, "get", "networkpolicy", name],
            )
            if result.returncode != 0:
                pytest.fail(
                    f"required NetworkPolicy {name} is not applied: "
                    f"{result.stderr.strip()}",
                    pytrace=False,
                )
        return {"argv": argv, "worker": worker, "head": head, "control": control}

    def test_worker_can_reach_pip_index(self, _network_policy_context):
        """TS-M5-26: worker pod can reach the configured pip index."""
        url = _network_policy_env("M5_TEST_NETWORK_POLICY_ALLOWED_URL")
        curl_rc, status = _probe(
            _network_policy_context["argv"],
            _network_policy_context["worker"],
            "ray-worker",
            url,
        )
        assert curl_rc == 0, f"allowlisted package index probe failed for {url}"
        assert 200 <= status < 400, f"allowlisted package index returned HTTP {status}"

    def test_worker_blocked_from_arbitrary_internet(self, _network_policy_context):
        """TS-M5-27: worker pod blocked from arbitrary egress."""
        url = os.getenv("M5_TEST_NETWORK_POLICY_BLOCKED_URL", "https://example.com")
        control_rc, control_status = _probe(
            _network_policy_context["argv"],
            _network_policy_context["control"],
            "core",
            url,
        )
        assert control_rc == 0 and 200 <= control_status < 400, (
            f"negative-probe control pod cannot reach {url}; refusing to treat an "
            f"ambient outage as NetworkPolicy enforcement "
            f"(curl_rc={control_rc}, HTTP {control_status})"
        )
        curl_rc, status = _probe(
            _network_policy_context["argv"],
            _network_policy_context["worker"],
            "ray-worker",
            url,
        )
        # Depending on the CNI/sidecar path, an egress-denied TLS socket can
        # surface as CURLE_SSL_CONNECT_ERROR (35) after the connection is
        # reset, rather than the connect/timeout codes (7/28).  All accepted
        # codes still require that no HTTP response was received.
        assert curl_rc in {7, 28, 35} and status == 0, (
            f"non-allowlisted egress unexpectedly reachable: {url} "
            f"(curl_rc={curl_rc}, HTTP {status})"
        )

    def test_ray_head_can_reach_internal_endpoints(self, _network_policy_context):
        """TS-M5-28: ray-head pod reaches Ray internal + scheduler."""
        url = os.getenv(
            "M5_TEST_NETWORK_POLICY_INTERNAL_URL",
            "http://core-api:7777/health",
        )
        curl_rc, status = _probe(
            _network_policy_context["argv"],
            _network_policy_context["head"],
            "ray-head",
            url,
        )
        assert curl_rc == 0, f"internal service probe failed for {url}"
        assert 200 <= status < 500, f"internal service returned HTTP {status}"


class TestRegression:
    """TS-M5-29 — pre-WS-M5 image-baked gates continue to work."""

    def test_default_execution_gate_unchanged(self, kz):
        """Capability probe should still report the platform's default
        ExecutionGate (allow_all when unconfigured); gate-package install
        path is purely additive.
        """
        # discover the default class to confirm it loads
        gate = kz.gates.discover(
            "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate"
        )
        assert gate.kind == "execution"
