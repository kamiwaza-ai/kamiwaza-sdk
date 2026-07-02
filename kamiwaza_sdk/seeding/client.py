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

    This is SDK-local request scoping for automation: the returned client adds
    the explicit workroom scope header to each request. It does not call
    ``workrooms.enter`` or mutate server-side selected-session binding.
    """
    return client.workroom_scope(workroom_id)


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
