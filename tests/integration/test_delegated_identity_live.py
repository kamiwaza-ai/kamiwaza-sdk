"""Live delegated-workload identity check against an enabled Core release.

This is intentionally not capability-mapped until an enabled, pinned release
has actually run it. The fake-provider contract suite cannot establish that a
real workload assertion is accepted by Core.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
import requests

from kamiwaza_sdk.delegated_workloads.proof import WorkloadProof
from kamiwaza_sdk.delegated_workloads.readiness import (
    ComponentStatus,
    ReadinessClient,
)
from kamiwaza_sdk.delegated_workloads.transport import (
    DelegatedWorkloadTransport,
    ResponsePort,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

_PROJECTED_ASSERTION = Path("/var/run/secrets/kamiwaza.ai/workload-identity/token")
_REQUEST_TIMEOUT_SECONDS = 15


class _BoundedSession:
    """Bound SDK requests and prevent assertion forwarding on redirects."""

    def __init__(self, session: requests.Session) -> None:
        self._session = session

    def request(self, method: str, url: str, **kwargs: object) -> ResponsePort:
        kwargs.setdefault("timeout", _REQUEST_TIMEOUT_SECONDS)
        kwargs.setdefault("verify", self._session.verify)
        kwargs.setdefault("allow_redirects", False)
        return cast(
            ResponsePort,
            self._session.request(method, url, **cast(Any, kwargs)),
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
    if os.getenv("KAMIWAZA_HTTP_TRACE_FILE", "").strip() or os.getenv(
        "KAMIWAZA_HTTP_TRACE", ""
    ).strip().lower() in {"1", "true", "yes", "on"}:
        pytest.skip(
            "integration HTTP tracing records raw headers; disable it before "
            "sending a projected workload assertion or DPoP proof"
        )
    api_root = live_base_url.rstrip("/")
    assert api_root.endswith("/api"), "live base URL must point at Core's /api"
    assert (
        urlsplit(api_root).username is None
    ), "live base URL must not embed credentials"
    base_url = api_root + "/v1/delegated-workloads"
    with requests.Session() as session:
        # No netrc credentials or proxy for this identity probe.
        session.trust_env = False
        verify_ssl = os.getenv("KAMIWAZA_VERIFY_SSL", "true").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        if verify_ssl:
            session.verify = (
                os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("CURL_CA_BUNDLE") or True
            )
        else:
            session.verify = False
        anonymous = session.get(
            base_url + "/capabilities",
            timeout=_REQUEST_TIMEOUT_SECONDS,
            verify=session.verify,
            allow_redirects=False,
        )
        assert not {
            "Authorization",
            "X-Kamiwaza-Workload-Assertion",
            "DPoP",
        }.intersection(anonymous.request.headers), "negative control sent credentials"
        assert anonymous.status_code in {400, 401, 403}, (
            "untrusted caller was not explicitly rejected by the enabled "
            f"delegated-workload route: HTTP {anonymous.status_code}"
        )
        assert _PROJECTED_ASSERTION.is_file(), (
            "the approved pod must mount its projected workload assertion at "
            f"{_PROJECTED_ASSERTION}"
        )

        proof = WorkloadProof.kubernetes(profile)
        transport = DelegatedWorkloadTransport(_BoundedSession(session), proof=proof)
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
    assert (
        discovery.role_resolution == "observed"
    ), "Core must resolve the registered workload role, not merely parse its proof"
    assert discovery.contract_versions
