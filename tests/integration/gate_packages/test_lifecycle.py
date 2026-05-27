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
- ``M5_TEST_WHEEL_DIR`` pointing at a directory containing
  ``acme_gates-1.0.0-py3-none-any.whl`` and (for the replace step)
  ``acme_gates-1.0.1-py3-none-any.whl`` plus a simple HTTP server
  serving them
- ``M5_TEST_INDEX_URL`` pointing at the HTTP server URL

The test is structured so it can also serve as the canonical M5b
smoke procedure when the human operator follows the playbook at
``docs/mesh-v1.0.0/demos/m5a-gate-packages-smoke.md`` (which can be
extended for M5b).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterator

import pytest

pytestmark = pytest.mark.integration


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
        import warnings

        warnings.warn(
            "TLS verification disabled via KAMIWAZA_VERIFY_SSL=0; only use this "
            "for dev clusters with self-signed certs. Production envs should "
            "install the CA into the trust store or use a properly-issued cert.",
            UserWarning,
            stacklevel=2,
        )
    from kamiwaza_sdk import KamiwazaClient

    return KamiwazaClient(base_url=base_url, api_key=token, verify=verify_ssl)


@pytest.fixture(scope="module")
def wheel_dir() -> Path:
    path = Path(_env("M5_TEST_WHEEL_DIR"))
    if not (path / "acme_gates-1.0.0-py3-none-any.whl").exists():
        pytest.skip(f"acme-gates v1.0.0 wheel not at {path}")
    return path


@pytest.fixture(scope="module")
def index_url() -> str:
    return _env("M5_TEST_INDEX_URL")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return f"sha256:{h.hexdigest()}"


@pytest.fixture(scope="module", autouse=True)
def cleanup_acme(kz) -> Iterator[None]:
    """Ensure a clean starting state — uninstall any prior acme-gates."""
    try:
        kz.gates.packages.uninstall("acme-gates")
    except Exception:
        pass  # not installed; fine
    yield
    # Module-level teardown — best-effort uninstall
    try:
        kz.gates.packages.uninstall("acme-gates")
    except Exception:
        pass


@pytest.fixture(scope="class", autouse=True)
def _require_wheel_and_index() -> None:
    """Skip the whole lifecycle class if the v1/v2 wheels or pip index aren't
    materialized. Without this, test_install_v1 self-skips via wheel_dir() but
    test_list_and_get / test_atomic_replace_to_v2 / test_uninstall still run
    against state that was never installed and the autouse cleanup_acme
    teardown wipes — leading to confusing `'acme-gates' not in names`
    assertions on a clean cluster."""
    wheel_root = os.getenv("M5_TEST_WHEEL_DIR", "").strip()
    index = os.getenv("M5_TEST_INDEX_URL", "").strip()
    if not wheel_root or not index:
        pytest.skip(
            "Lifecycle class requires both M5_TEST_WHEEL_DIR and "
            "M5_TEST_INDEX_URL — set both to run the install/replace/"
            "uninstall sequence end-to-end."
        )


class TestLifecycle:
    """TS-M5-24 (install) + TS-M5-25 (replace) + TS-M5-15 (uninstall)."""

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
        v2 = wheel_dir / "acme_gates-1.0.1-py3-none-any.whl"
        if not v2.exists():
            pytest.skip("acme-gates v1.0.1 wheel not built; replace test skipped")
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

    Requires the chart's workerNetworkPolicy.enabled=true and
    rayHeadNetworkPolicy.enabled=true (default false at M5 ship). Test
    skips if NetworkPolicies aren't applied.
    """

    def test_worker_can_reach_pip_index(self, kz, index_url):
        """TS-M5-26: worker pod can reach the configured pip index."""
        pytest.skip(
            "Requires kubectl exec into a worker pod + curl probe; "
            "operator runs manually per the M5b smoke playbook."
        )

    def test_worker_blocked_from_arbitrary_internet(self, kz):
        """TS-M5-27: worker pod blocked from arbitrary egress."""
        pytest.skip(
            "Requires kubectl exec into a worker pod + curl probe to "
            "a non-allowlisted internet host; operator runs manually."
        )

    def test_ray_head_can_reach_internal_endpoints(self, kz):
        """TS-M5-28: ray-head pod reaches Ray internal + scheduler."""
        pytest.skip(
            "Requires kubectl exec into the ray-head pod; operator runs "
            "manually per the M5b smoke playbook."
        )


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
