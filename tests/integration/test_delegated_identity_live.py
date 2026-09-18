"""Live delegated-workload identity check against an enabled Core release.

This is intentionally not capability-mapped until an enabled, pinned release
has actually run it. The fake-provider contract suite cannot establish that a
real workload assertion is accepted by Core.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from kamiwaza_sdk.delegated_workloads.proof import WorkloadProof
from kamiwaza_sdk.delegated_workloads.readiness import (
    ComponentStatus,
    ReadinessClient,
)
from kamiwaza_sdk.delegated_workloads.transport import DelegatedWorkloadTransport

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

_PROJECTED_ASSERTION = Path(
    "/var/run/secrets/kamiwaza.ai/workload-identity/token"
)


def test_projected_workload_identity_discovers_and_rejects_anonymous_caller(
    live_base_url: str,
) -> None:
    """An attested pod can discover while a caller without its proof cannot."""
    profile = os.getenv("KAMIWAZA_DELEGATED_ATTESTATION_PROFILE", "").strip()
    if not profile:
        pytest.skip(
            "requires enabled delegated-workload policy, a registered disposable "
            "workload identity, and KAMIWAZA_DELEGATED_ATTESTATION_PROFILE"
        )
    api_root = live_base_url.rstrip("/")
    assert api_root.endswith("/api"), "live base URL must point at Core's /api"
    base_url = api_root + "/v1/delegated-workloads"
    with requests.Session() as session:
        session.verify = os.getenv("KAMIWAZA_VERIFY_SSL", "true").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        anonymous = session.get(base_url + "/capabilities", timeout=15)
        assert anonymous.status_code in {400, 401, 403}, (
            "untrusted caller was not explicitly rejected by the enabled "
            f"delegated-workload route: HTTP {anonymous.status_code}"
        )
        assert _PROJECTED_ASSERTION.is_file(), (
            "the approved pod must mount its projected workload assertion at "
            f"{_PROJECTED_ASSERTION}"
        )

        proof = WorkloadProof.kubernetes(profile)
        transport = DelegatedWorkloadTransport(session, proof=proof)
        try:
            discovery = ReadinessClient(
                base_url,
                transport,
                clock=lambda: datetime.now(timezone.utc),
            ).discover()
        finally:
            transport.close()

    assert profile in discovery.attestation_profiles
    assert discovery.attestation_profile_status[profile].status is ComponentStatus.READY
    assert discovery.contract_versions
