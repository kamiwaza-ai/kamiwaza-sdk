"""GHCR resolve sanity-check for OPERATOR_COMPATIBLE_TAGS (TS-13).

Out-of-band check that every tag in :data:`OPERATOR_COMPATIBLE_TAGS`
resolves at GHCR. Marked ``integration`` so it does not run in the default
``make test`` path; runs as part of release CI to catch a list that drifted
from what was actually published.

Design reference: §4.2.16 OperatorImagePin maintenance contract.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

import pytest
import requests

from kamiwaza_extensions.platform_compat import (
    OPERATOR_COMPATIBLE_TAGS,
    OPERATOR_IMAGE,
)

# This test intentionally talks to GHCR, so it must opt out of the
# pytest-responses HTTP mock while remaining in the integration lane.
pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]

# OPERATOR_IMAGE is "ghcr.io/<owner>/<repo>" — split into the registry path
# expected by GHCR's OCI distribution API.
_GHCR_HOST = "ghcr.io"
_OWNER_REPO = OPERATOR_IMAGE.removeprefix(f"{_GHCR_HOST}/")
_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)


def _github_username() -> str:
    if actor := os.environ.get("GITHUB_ACTOR"):
        return actor
    try:
        return subprocess.check_output(
            ["gh", "api", "user", "--jq", ".login"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "x-access-token"


def _docker_ghcr_credentials() -> tuple[str, str] | None:
    config_path = Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker"))
    if config_path.is_dir():
        config_path = config_path / "config.json"
    try:
        auths = json.loads(config_path.read_text()).get("auths", {})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    ghcr_auth = auths.get(_GHCR_HOST) or auths.get(f"https://{_GHCR_HOST}")
    if not isinstance(ghcr_auth, dict) or "auth" not in ghcr_auth:
        return None
    try:
        decoded = base64.b64decode(ghcr_auth["auth"]).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep or not password:
        return None
    return username or "x-access-token", password


def _ghcr_basic_auth() -> tuple[str, str] | None:
    token = (
        os.environ.get("GHCR_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
    )
    if token:
        return _github_username(), token
    return _docker_ghcr_credentials()


def _ghcr_token(scope: str) -> str | None:
    """Fetch a GHCR registry token, using local credentials when available."""
    kwargs = {}
    if basic_auth := _ghcr_basic_auth():
        kwargs["auth"] = basic_auth
    resp = requests.get(
        f"https://{_GHCR_HOST}/token",
        params={"scope": f"repository:{scope}:pull"},
        timeout=10,
        **kwargs,
    )
    if not resp.ok:
        return None
    return resp.json().get("token")


@pytest.mark.integration
@pytest.mark.parametrize("tag", OPERATOR_COMPATIBLE_TAGS)
def test_compatible_tag_resolves_at_ghcr(tag: str) -> None:
    if os.environ.get("KAMIWAZA_SKIP_GHCR_CHECK"):
        pytest.skip("KAMIWAZA_SKIP_GHCR_CHECK set")

    headers = {"Accept": _MANIFEST_ACCEPT}
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
