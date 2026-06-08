"""GHCR resolve sanity-check for OPERATOR_COMPATIBLE_TAGS (TS-13).

Out-of-band check that every tag in :data:`OPERATOR_COMPATIBLE_TAGS`
resolves at GHCR. Marked ``integration`` so it does not run in the default
``make test`` path; runs as part of release CI to catch a list that drifted
from what was actually published.

Design reference: §4.2.16 OperatorImagePin maintenance contract.
"""

from __future__ import annotations

import os

import pytest
import requests

from kamiwaza_extensions.platform_compat import (
    OPERATOR_COMPATIBLE_TAGS,
    OPERATOR_IMAGE,
)

# The module docstring already says "Marked ``integration`` so it does not run
# in the default ``make test`` path" — but the marker had drifted away. This
# restores it so the GHCR resolve sanity-check stays out of the unit lane.
pytestmark = pytest.mark.integration

# OPERATOR_IMAGE is "ghcr.io/<owner>/<repo>" — split into the registry path
# expected by GHCR's OCI distribution API.
_GHCR_HOST = "ghcr.io"
_OWNER_REPO = OPERATOR_IMAGE.removeprefix(f"{_GHCR_HOST}/")


def _ghcr_token(scope: str) -> str | None:
    """Fetch an anonymous read token for a GHCR repo, if the repo is public."""
    resp = requests.get(
        f"https://{_GHCR_HOST}/token",
        params={"scope": f"repository:{scope}:pull"},
        timeout=10,
    )
    if not resp.ok:
        return None
    return resp.json().get("token")


@pytest.mark.integration
@pytest.mark.parametrize("tag", OPERATOR_COMPATIBLE_TAGS)
def test_compatible_tag_resolves_at_ghcr(tag: str) -> None:
    if os.environ.get("KAMIWAZA_SKIP_GHCR_CHECK"):
        pytest.skip("KAMIWAZA_SKIP_GHCR_CHECK set")

    headers = {"Accept": "application/vnd.oci.image.manifest.v1+json"}
    token = _ghcr_token(_OWNER_REPO)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = requests.head(
        f"https://{_GHCR_HOST}/v2/{_OWNER_REPO}/manifests/{tag}",
        headers=headers,
        timeout=15,
    )

    if resp.status_code == 401:
        pytest.skip(
            f"GHCR repo {_OWNER_REPO} requires authentication; cannot verify "
            "from anonymous CI runner."
        )

    assert resp.status_code == 200, (
        f"OPERATOR_COMPATIBLE_TAGS contains {tag!r} but it does not resolve "
        f"at {_GHCR_HOST}/{_OWNER_REPO}:{tag} (HTTP {resp.status_code})"
    )
