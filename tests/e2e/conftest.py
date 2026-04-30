"""Shared fixtures for the D210 scenario harness.

Drivers under ``tests/e2e/scenarios/`` skip cleanly when the staging
environment is not configured. Set ``KAMIWAZA_STAGING_URL`` (and an
auth credential the harness can pick up — typically a PAT in
``KAMIWAZA_STAGING_PAT``) to enable a real run.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def staging_url() -> str:
    """Resolve the staging cluster URL or skip the test session.

    A scenario driver depending on this fixture is automatically skipped
    when no staging cluster is configured — useful for CI runs that
    should not exercise live infrastructure.
    """
    url = os.environ.get("KAMIWAZA_STAGING_URL")
    if not url:
        pytest.skip(
            "KAMIWAZA_STAGING_URL not set — scenario driver requires a "
            "staging cluster (see tests/e2e/scenarios/harness.py)"
        )
    return url
