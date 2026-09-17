"""Single-cluster live access-tier gate retrieval.

Proves the full install, file source, gate binding, and invocation path on one
live cluster. The deterministic five-row dataset contains three basic rows, one
standard row, and one advanced row. Each persona receives exactly the rows its
access tier permits.

The test skips unless the operator provisions:

* the gate-packages PVC and ``M5_TEST_KUBECTL`` for automatic session setup;
* a filesystem source under ``RETRIEVAL_FILESYSTEM_ALLOWED_ROOTS``, exposed
  through ``ACCESS_TIER_DATASET_PATH``.
"""

from __future__ import annotations

import os
from typing import Iterator

import pytest

from . import _access_tier as mc

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

_PERSONAS = {
    "basic": "access-basic",
    "standard": "access-standard",
    "advanced": "access-advanced",
}


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
    dataset_path = os.getenv("ACCESS_TIER_DATASET_PATH", "").strip()
    if not dataset_path:
        pytest.skip(
            "ACCESS_TIER_DATASET_PATH not set — place the five-row fixture "
            "under RETRIEVAL_FILESYSTEM_ALLOWED_ROOTS and set this variable"
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

    mc.declare_access_tier_attribute(kz)
    mc.install_gate_package(kz, wheel_dir, index_url)
    urn = mc.create_file_dataset(kz, _unique("access-tier"), dataset_path)

    for access_tier, username in _PERSONAS.items():
        mc.seed_local_persona(kz, _unique(username), access_tier)
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


@pytest.mark.parametrize("access_tier", ["basic", "standard", "advanced"])
def test_persona_sees_exact_post_gate_counts(
    access_tier, gated_dataset, _admin
) -> None:
    """Each persona retrieves exactly the rows its access tier permits."""
    base_url, _admin_client, verify = _admin
    username = _unique(_PERSONAS[access_tier])
    client = mc.authed_client(base_url, username, username, verify=verify)

    rows, gate_audit = mc.retrieve_through_gate(client, gated_dataset)
    mc.assert_persona_result(access_tier, rows, gate_audit)
