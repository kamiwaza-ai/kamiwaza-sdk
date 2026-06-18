"""Unit coverage for the peer-auth precedence helper (ENG-7284 review M#2).

Closes the "precedence path not exercised" gap: the password→peer-key fallback
is pure logic, tested here without a live server. Imports the co-located
``peer_auth`` module via the same path shim the integration conftest uses.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import peer_auth  # noqa: E402


def test_password_only_uses_password_no_probe() -> None:
    # Legacy fast path: no peer key, so no probe is run (password_probe_ok=None).
    assert (
        peer_auth.choose_peer_auth(has_password=True, has_peer_key=False)
        == peer_auth.PASSWORD
    )


def test_password_and_peer_key_prefers_password_when_probe_ok() -> None:
    assert (
        peer_auth.choose_peer_auth(
            has_password=True, has_peer_key=True, password_probe_ok=True
        )
        == peer_auth.PASSWORD
    )


def test_password_probe_failure_falls_back_to_peer_key() -> None:
    assert (
        peer_auth.choose_peer_auth(
            has_password=True, has_peer_key=True, password_probe_ok=False
        )
        == peer_auth.PEER_KEY
    )


def test_peer_key_only_uses_peer_key() -> None:
    assert (
        peer_auth.choose_peer_auth(has_password=False, has_peer_key=True)
        == peer_auth.PEER_KEY
    )


def test_no_credentials_skips() -> None:
    assert (
        peer_auth.choose_peer_auth(has_password=False, has_peer_key=False)
        == peer_auth.SKIP
    )
