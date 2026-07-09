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
from ..exceptions import APIError


def _is_binding_unsupported(error: APIError) -> bool:
    """True when ``enter`` was rejected because the credential can't session-bind.

    PAT / API-key credentials authorize workrooms via the explicit
    ``X-Workroom-Id`` header, not a selected-session binding, so the platform
    rejects their ``enter`` with 409 ``binding_invalid``. Session (password
    grant) tokens bind successfully instead. Mirrors the live-test probe in
    ``tests/integration/test_seeding_live.py``.
    """
    if getattr(error, "status_code", None) != 409:
        return False
    payload = getattr(error, "response_data", None)
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        structured = detail.get("error")
        if isinstance(structured, dict):
            return structured.get("class") == "binding_invalid"
    return detail == "workroom_binding_invalid"


def scoped_client_for_workroom(
    client: KamiwazaClient, workroom_id: Union[str, UUID]
) -> KamiwazaClient:
    """Return a client scoped to ``workroom_id``, binding its session when able.

    Automation performing per-workroom *writes* (install a workroom extension,
    create an agent/conversation) needs both:

    1. the explicit ``X-Workroom-Id`` scope header on every request
       (``client.workroom_scope``), and
    2. a server-side selected-workroom binding, established by
       ``workrooms.enter``.

    The platform no longer authorizes workroom writes from the scope header
    alone (strict ForwardAuth / ``KAMIWAZA_ALLOW_LEGACY_WORKROOM_HEADER=false``);
    a session token that only sets the header gets 403 "Authenticated workroom
    context missing" on ``deploy_app`` / ``create-agent``. ``enter`` binds the
    session server-side (it returns no token; the binding is keyed to the
    caller's session) so those writes carry authenticated workroom context.

    ``enter`` is issued on an explicitly **unscoped** view of the client
    (``workroom_scope(None)`` strips any ``X-Workroom-Id`` the caller may have
    already set) on purpose: ``enter`` is a POST, so it passes through the strict
    workroom-write gate, and a request that carries the scope header before any
    binding exists is rejected with 403 "Authenticated workroom context missing"
    (the bind is what would create that context). The unscoped enter has no
    scope header, binds the session, and only then is the header-scoped client
    returned for the actual writes.

    PAT / API-key credentials cannot session-bind — the platform rejects their
    ``enter`` with 409 ``binding_invalid`` — and authorize via the explicit
    header instead, so that specific rejection is tolerated and the
    header-scoped client is returned unchanged. Any other error (e.g. a 404 for
    an unknown workroom) propagates.
    """
    try:
        client.workroom_scope(None).workrooms.enter(workroom_id)
    except APIError as exc:
        if not _is_binding_unsupported(exc):
            raise
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
