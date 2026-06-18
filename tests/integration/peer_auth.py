"""Peer-cluster auth precedence for the two-cluster live suite (ENG-7284 M#2).

The ``live_kamiwaza_peer_client`` fixture does the network probe; the pure
precedence decision lives here so it is unit-testable without a live server.
Co-located with the integration conftest (imported via the same ``sys.path``
shim as ``capability_markers``).
"""

from __future__ import annotations

from typing import Optional

PASSWORD = "password"
PEER_KEY = "peer_key"
SKIP = "skip"


def choose_peer_auth(
    *,
    has_password: bool,
    has_peer_key: bool,
    password_probe_ok: Optional[bool] = None,
) -> str:
    """Decide which credential the peer client should use.

    Precedence (admin ops on the peer, e.g. ``federations.pair``):
      - Password only (no peer key): ``PASSWORD`` — the legacy fast path; the
        fixture skips the eager probe and keeps lazy auth, so
        ``password_probe_ok`` is ``None`` here.
      - Password AND peer key: ``PASSWORD`` if the eager probe succeeded, else
        ``PEER_KEY`` — the primary admin password need not match the peer's, so
        fall back to the explicitly-supplied peer credential when it fails.
      - Peer key only: ``PEER_KEY``.
      - Neither: ``SKIP``.
    """
    if has_password and not has_peer_key:
        return PASSWORD
    if has_password and has_peer_key:
        return PASSWORD if password_probe_ok else PEER_KEY
    if has_peer_key:
        return PEER_KEY
    return SKIP
