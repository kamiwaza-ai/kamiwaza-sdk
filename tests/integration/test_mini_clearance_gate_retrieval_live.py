"""ENG-8325 Layer 2 — single-cluster live gated-retrieval (no federation).

Proves the full install -> file-source -> gate-bind -> invoke path on ONE live
cluster (a gate is a dataset feature, so the data-plane is provable without a
mesh): install the acme-gates==1.1.0 wheel, back a ``platform="file"`` dataset
with the deterministic 5-row [U,U,U,S,TS] parquet/csv, bind MiniClearanceGate,
then retrieve as U/S/TS personas and assert the exact post-gate counts
(U:3/2, S:4/1, TS:5/0) with zero leakage.

Soft-skips (contributor boxes stay green) unless the operator has provisioned:
  * the WS-M5 gate-packages PVC, and a served 1.1.0 wheel/index
    (M5_TEST_WHEEL_DIR + M5_TEST_INDEX_URL, per gate_packages/test_lifecycle.py);
  * a filesystem-source file the ray-head can read: place the fixture (see
    _mini_clearance.write_dataset_file) at an absolute path under the cluster's
    RETRIEVAL_FILESYSTEM_ALLOWED_ROOTS and pass it via MINI_CLEARANCE_DATASET_PATH.
"""

from __future__ import annotations

import os
from typing import Iterator

import pytest

from . import _mini_clearance as mc

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

_PERSONAS = {"U": "mini-clr-u", "S": "mini-clr-s", "TS": "mini-clr-ts"}


def _unique(base: str) -> str:
    # Per-worker/per-run uniqueness without RNG/clock in the assertion path.
    return f"{base}-{os.getpid()}"


@pytest.fixture(scope="module")
def _prereqs() -> tuple[str, str, str]:
    wi = mc.wheel_and_index()
    if wi is None:
        pytest.skip(
            "gate-packages wheel/index not configured (set M5_TEST_WHEEL_DIR + "
            "M5_TEST_INDEX_URL to the served acme-gates 1.1.0 wheel)"
        )
    dataset_path = os.getenv("MINI_CLEARANCE_DATASET_PATH", "").strip()
    if not dataset_path:
        pytest.skip(
            "MINI_CLEARANCE_DATASET_PATH not set — place the 5-row fixture "
            "(parquet/csv) at an absolute path under the cluster's "
            "RETRIEVAL_FILESYSTEM_ALLOWED_ROOTS and point this env var at it"
        )
    return wi[0], wi[1], dataset_path


def _verify() -> bool:
    return os.getenv("KAMIWAZA_VERIFY_SSL", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


@pytest.fixture(scope="module")
def _admin(request) -> tuple:
    """(base_url, module-scoped admin client, verify) from the --live-* options.

    Built directly from config rather than the function-scoped
    live_kamiwaza_client, so the one-time install/seed setup can be module-scoped.
    """
    base = str(request.config.getoption("live_base_url")).rstrip("/")
    user = str(request.config.getoption("live_username"))
    pw = str(request.config.getoption("live_password"))
    if not base or not pw:
        pytest.skip("live cluster base-url/password not provided")
    verify = _verify()
    return base, mc.authed_client(base, user, pw, verify=verify), verify


@pytest.fixture(scope="module")
def gated_dataset(_prereqs, _admin) -> Iterator[str]:
    """Install the gate, create the file dataset, bind the gate, seed personas.

    Yields the dataset URN; tears everything down at module exit.
    """
    wheel_dir, index_url, dataset_path = _prereqs
    _base, kz, _v = _admin

    mc.declare_clearance_attribute(kz)
    mc.install_gate_package(kz, wheel_dir, index_url)
    urn = mc.create_file_dataset(kz, _unique("mini-clearance"), dataset_path)

    for clearance, username in _PERSONAS.items():
        mc.seed_local_persona(kz, _unique(username), clearance)
        mc.grant_dataset_viewer(kz, _unique(username), urn)

    try:
        yield urn
    finally:
        for username in _PERSONAS.values():
            try:
                kz.subjects.delete(_unique(username), cascade_grants=True)
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
        try:
            kz.datasets.delete(urn)
        except Exception:  # noqa: BLE001
            pass
        try:
            kz.gates.packages.uninstall("acme-gates")
        except Exception:  # noqa: BLE001
            pass


@pytest.mark.parametrize("clearance", ["U", "S", "TS"])
def test_persona_sees_exact_post_gate_counts(clearance, gated_dataset, _admin) -> None:
    """Each clearance persona retrieves exactly its allowed rows — known answer."""
    base_url, _admin_client, verify = _admin
    username = _unique(_PERSONAS[clearance])
    client = mc.authed_client(base_url, username, username, verify=verify)

    rows, gate_audit = mc.retrieve_through_gate(client, gated_dataset)
    mc.assert_persona_result(clearance, rows, gate_audit)
