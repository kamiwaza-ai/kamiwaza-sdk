# kamiwaza_sdk/seeding/client.py

"""Build an authenticated client from the environment.

A single place to construct a :class:`KamiwazaClient` for on-box seeding and
off-box smoke runs, so both draw from one helper instead of re-deriving auth
and TLS settings. Honors the same env vars the client and live tests already
use (``KAMIWAZA_BASE_URL`` / ``KAMIWAZA_API_KEY`` / ``KAMIWAZA_VERIFY_SSL``).
"""

import os
from typing import Optional, Union
from uuid import UUID

from ..client import KamiwazaClient


def scoped_client_for_workroom(
    client: KamiwazaClient, workroom_id: Union[str, UUID]
) -> KamiwazaClient:
    """Return a client whose calls are scoped to ``workroom_id``.

    Workroom *enter* re-mints a JWT carrying the workroom claim; using that
    token scopes subsequent platform deploys and Kaizen agent creation via the
    trusted identity (``identity.workroom_id``) — the durable path, rather than
    the legacy ``X-Workroom-Id`` transport hint. Falls back to the original
    client when the platform does not remint a token (e.g. the Global workroom).
    """
    response = client.workrooms.enter(workroom_id)
    token = getattr(response, "access_token", None)
    if not token:
        return client
    scoped = KamiwazaClient(base_url=client.base_url, api_key=token)
    # Carry over the TLS setting (e.g. self-signed dev cert) from the parent.
    scoped.session.verify = client.session.verify
    return scoped


def build_client_from_env(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> KamiwazaClient:
    """Construct an authenticated client from explicit args or the environment.

    Args:
        base_url: Override for ``KAMIWAZA_BASE_URL`` / ``KAMIWAZA_BASE_URI``.
        api_key: Override for ``KAMIWAZA_API_KEY`` / ``KAMIWAZA_API_TOKEN``.

    Returns:
        A KamiwazaClient. TLS verification follows ``KAMIWAZA_VERIFY_SSL``
        (the client honors it natively), which the on-box nightly sets for the
        self-signed cluster cert.
    """
    resolved_base = (
        base_url
        or os.environ.get("KAMIWAZA_BASE_URL")
        or os.environ.get("KAMIWAZA_BASE_URI")
    )
    if not resolved_base:
        raise SystemExit(
            "Set KAMIWAZA_BASE_URL (or pass --base-url) to point the seeder at a cluster."
        )
    return KamiwazaClient(base_url=resolved_base, api_key=api_key)
