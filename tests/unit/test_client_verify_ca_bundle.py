"""T7.13 / ENG-5047 — verify= and ca_bundle= constructor kwargs on
KamiwazaClient (closes ENG-5015).

WS-M3.2 wire-up. The legacy KamiwazaClient already honored
KAMIWAZA_VERIFY_SSL env var via the requests.Session default behavior;
T7.13 adds the explicit constructor kwargs so callers can be programmatic
(no environment manipulation) and document the precedence:

    explicit ca_bundle= > explicit verify= > KAMIWAZA_VERIFY_SSL env >
    REQUESTS_CA_BUNDLE env (honored by requests.Session natively) >
    default True (system bundle)

This closes the M4 UAT-discovered gap (ENG-5015) where the canonical
client surface lacked an in-Python way to point at a self-signed cluster
CA bundle.
"""

from __future__ import annotations

import pytest

from kamiwaza_sdk.client import KamiwazaClient

pytestmark = pytest.mark.unit


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAMIWAZA_BASE_URL", raising=False)
    monkeypatch.delenv("KAMIWAZA_BASE_URI", raising=False)
    monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)


def test_default_verify_is_true_when_no_kwargs_no_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No kwargs, no env vars — Session.verify defaults to True (system bundle)."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://example.test/api")
    client = KamiwazaClient()
    assert client.session.verify is True


def test_explicit_verify_false_disables_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KamiwazaClient(verify=False) → Session.verify=False, no env required."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://example.test/api")
    client = KamiwazaClient(verify=False)
    assert client.session.verify is False


def test_explicit_verify_path_sets_ca_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """KamiwazaClient(verify="/path/to/ca.pem") → Session.verify=path."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://example.test/api")
    client = KamiwazaClient(verify="/etc/ssl/cluster-ca.pem")
    assert client.session.verify == "/etc/ssl/cluster-ca.pem"


def test_explicit_ca_bundle_kwarg_sets_ca_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KamiwazaClient(ca_bundle="/path") sugar — equivalent to verify="/path"."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://example.test/api")
    client = KamiwazaClient(ca_bundle="/etc/ssl/cluster-ca.pem")
    assert client.session.verify == "/etc/ssl/cluster-ca.pem"


def test_ca_bundle_precedence_over_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both ca_bundle and verify are supplied, ca_bundle wins. Documents
    the precedence + lets callers reach for either name without surprise."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://example.test/api")
    client = KamiwazaClient(
        verify=False,
        ca_bundle="/etc/ssl/cluster-ca.pem",
    )
    # ca_bundle wins.
    assert client.session.verify == "/etc/ssl/cluster-ca.pem"


def test_explicit_kwarg_wins_over_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KamiwazaClient(verify=True) explicitly overrides KAMIWAZA_VERIFY_SSL=false."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://example.test/api")
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
    client = KamiwazaClient(verify=True)
    assert client.session.verify is True


def test_env_var_used_when_no_explicit_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KAMIWAZA_VERIFY_SSL=false → Session.verify=False when caller doesn't
    override. Preserves existing behavior (regression guard)."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://example.test/api")
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")
    client = KamiwazaClient()
    assert client.session.verify is False
