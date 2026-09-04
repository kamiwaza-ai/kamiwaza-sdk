"""Offline contracts for the live-password fixture masking bug.

Reset-2 UAT live evidence: with a PAT-only session (``KAMIWAZA_API_KEY`` set,
no ``KAMIWAZA_PASSWORD``) and a checkout layout where no kz-login candidate
path resolved, ``resolved_live_password`` yielded ``""`` — and the
password-required CLI tests ran anyway, sending ``--password ''`` to the
platform. The wire body was ``password=`` (99 chars, field present but empty,
verified with ``keep_blank_values=True``) and ``POST /api/auth/token``
answered 422 ``password: Field required``, which reads like a platform
regression but is an unresolved-credential artifact. The end-to-end fixture
behavior is proven by the live CLI tests; these unit contracts pin the pure
decision helper and the kz-login candidate paths.
"""

from __future__ import annotations

import pytest

from tests.integration import conftest as live_conftest

pytestmark = pytest.mark.unit

_UNRESOLVED_ERROR = "kz-login fallback unavailable; configured password is empty"


def test_password_gate_skips_on_unresolved_password() -> None:
    """A PAT must not mask unresolved password auth for password-required tests.

    The gate skips with the redacted resolver error instead of handing the
    test an empty password (which would reach the wire as ``password=`` and
    produce a misleading 422 ``Field required`` from ``/auth/token``).
    """
    with pytest.raises(pytest.skip.Exception, match="Password-required live tests"):
        live_conftest._require_resolved_live_password("", _UNRESOLVED_ERROR)


def test_password_gate_skips_on_whitespace_password() -> None:
    with pytest.raises(pytest.skip.Exception, match=_UNRESOLVED_ERROR):
        live_conftest._require_resolved_live_password("   ", _UNRESOLVED_ERROR)


def test_password_gate_returns_resolved_password() -> None:
    assert (
        live_conftest._require_resolved_live_password(
            "resolved-secret", _UNRESOLVED_ERROR
        )
        == "resolved-secret"
    )


def test_kz_login_candidate_covers_kamiwaza_root_at_deploy_root(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KAMIWAZA_ROOT pointing at the deploy repo root must resolve kz-login.

    The documented harness ``.env.local`` sets ``KAMIWAZA_ROOT`` to the deploy
    repo root (``.../deploy``); joining ``deploy/scripts/kz-login`` onto that
    doubles the segment (``.../deploy/deploy/scripts/kz-login``) and always
    misses, silently dropping password resolution for the whole session.
    ``scripts/kz-login`` under the same root must be tried as well.
    """
    deploy_root = tmp_path / "deploy"
    scripts = deploy_root / "scripts"
    scripts.mkdir(parents=True)
    kz_login = scripts / "kz-login"
    kz_login.write_text("#!/bin/sh\necho unit-password\n")
    kz_login.chmod(0o755)
    monkeypatch.setenv("KAMIWAZA_ROOT", str(deploy_root))
    assert live_conftest._resolve_kz_login_password() == "unit-password"
