"""Tests for static credential resolution."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kamiwaza_extensions.client.auth.chain import resolve_credentials
from kamiwaza_extensions.client.auth.static import (
    ENV_AWS_PROFILE,
    ENV_AWS_SHARED_CREDENTIALS_FILE,
    _load_aws_credentials_file,
    get_static_credentials,
)
from kamiwaza_extensions.client.config import ENV_ACCESS_KEY_ID, ENV_SECRET_ACCESS_KEY


def test_get_static_credentials_explicit() -> None:
    """Explicit credentials are returned when provided."""
    creds = get_static_credentials(
        access_key_id="explicit-key",
        secret_access_key="explicit-secret",
    )
    assert creds is not None
    assert creds.access_key_id == "explicit-key"
    assert creds.secret_access_key == "explicit-secret"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"access_key_id": "key"},
        {"secret_access_key": "secret"},
        {"session_token": "token"},
    ],
)
def test_get_static_credentials_rejects_partial_explicit_credentials(
    kwargs: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="must be provided together|requires explicit"):
        get_static_credentials(**kwargs)


def test_get_static_credentials_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2 environment variables provide credentials when no explicit or AWS file."""
    monkeypatch.setenv(ENV_AWS_SHARED_CREDENTIALS_FILE, "/nonexistent")  # skip AWS file
    monkeypatch.setenv(ENV_ACCESS_KEY_ID, "env-key")
    monkeypatch.setenv(ENV_SECRET_ACCESS_KEY, "env-secret")

    creds = get_static_credentials()
    assert creds is not None
    assert creds.access_key_id == "env-key"
    assert creds.secret_access_key == "env-secret"


def test_get_static_credentials_explicit_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit credentials override environment."""
    monkeypatch.setenv(ENV_ACCESS_KEY_ID, "env-key")
    monkeypatch.setenv(ENV_SECRET_ACCESS_KEY, "env-secret")

    creds = get_static_credentials(
        access_key_id="explicit-key",
        secret_access_key="explicit-secret",
    )
    assert creds is not None
    assert creds.access_key_id == "explicit-key"


def test_get_static_credentials_none_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns None when no credentials available."""
    # Point to non-existent file so we don't pick up real ~/.aws/credentials
    monkeypatch.setenv(ENV_AWS_SHARED_CREDENTIALS_FILE, "/nonexistent/aws/credentials")
    creds = get_static_credentials()
    assert creds is None


def test_get_static_credentials_aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from env provide credentials."""
    monkeypatch.delenv(ENV_ACCESS_KEY_ID, raising=False)
    monkeypatch.delenv(ENV_SECRET_ACCESS_KEY, raising=False)
    monkeypatch.setenv(ENV_AWS_SHARED_CREDENTIALS_FILE, "/nonexistent")  # skip file
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "aws-env-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-env-secret")

    creds = get_static_credentials()
    assert creds is not None
    assert creds.access_key_id == "aws-env-key"
    assert creds.secret_access_key == "aws-env-secret"


def test_get_static_credentials_aws_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials loaded from ~/.aws/credentials when R2 and AWS env not set."""
    monkeypatch.delenv(ENV_ACCESS_KEY_ID, raising=False)
    monkeypatch.delenv(ENV_SECRET_ACCESS_KEY, raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write(
            "[default]\n"
            "aws_access_key_id = file-key\n"
            "aws_secret_access_key = file-secret\n"
        )
        creds_path = f.name
    try:
        monkeypatch.setenv(ENV_AWS_SHARED_CREDENTIALS_FILE, creds_path)
        creds = get_static_credentials()
        assert creds is not None
        assert creds.access_key_id == "file-key"
        assert creds.secret_access_key == "file-secret"
    finally:
        Path(creds_path).unlink(missing_ok=True)


def test_get_static_credentials_aws_file_overrides_r2_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """~/.aws/credentials takes precedence over R2_* env vars."""
    monkeypatch.delenv(ENV_ACCESS_KEY_ID, raising=False)
    monkeypatch.delenv(ENV_SECRET_ACCESS_KEY, raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write(
            "[default]\n"
            "aws_access_key_id = file-key\n"
            "aws_secret_access_key = file-secret\n"
        )
        creds_path = f.name
    try:
        monkeypatch.setenv(ENV_AWS_SHARED_CREDENTIALS_FILE, creds_path)
        monkeypatch.setenv(ENV_ACCESS_KEY_ID, "r2-env-key")
        monkeypatch.setenv(ENV_SECRET_ACCESS_KEY, "r2-env-secret")
        creds = get_static_credentials()
        assert creds is not None
        assert creds.access_key_id == "file-key"
        assert creds.secret_access_key == "file-secret"
    finally:
        Path(creds_path).unlink(missing_ok=True)


def test_get_static_credentials_aws_file_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AWS_PROFILE selects which profile to use from credentials file."""
    monkeypatch.delenv(ENV_ACCESS_KEY_ID, raising=False)
    monkeypatch.delenv(ENV_SECRET_ACCESS_KEY, raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write(
            "[default]\n"
            "aws_access_key_id = default-key\n"
            "aws_secret_access_key = default-secret\n"
            "[r2-dev]\n"
            "aws_access_key_id = dev-key\n"
            "aws_secret_access_key = dev-secret\n"
        )
        creds_path = f.name
    try:
        monkeypatch.setenv(ENV_AWS_SHARED_CREDENTIALS_FILE, creds_path)
        monkeypatch.setenv(ENV_AWS_PROFILE, "r2-dev")
        creds = get_static_credentials()
        assert creds is not None
        assert creds.access_key_id == "dev-key"
        assert creds.secret_access_key == "dev-secret"
    finally:
        Path(creds_path).unlink(missing_ok=True)


def test_load_aws_credentials_file_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """_load_aws_credentials_file returns None when file does not exist."""
    monkeypatch.setenv(ENV_AWS_SHARED_CREDENTIALS_FILE, "/nonexistent/aws/credentials")
    assert _load_aws_credentials_file("default") is None


def test_resolve_credentials_returns_static() -> None:
    """resolve_credentials returns static when explicit creds provided."""
    creds, method = resolve_credentials(
        access_key_id="key",
        secret_access_key="secret",
    )
    assert creds is not None
    assert method == "static"


def test_resolve_credentials_returns_sso_when_no_static(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_credentials returns sso when no static creds."""
    monkeypatch.setenv(ENV_AWS_SHARED_CREDENTIALS_FILE, "/nonexistent/aws/credentials")
    creds, method = resolve_credentials()
    assert creds is None
    assert method == "sso"


def test_resolve_credentials_forced_sso_ignores_static_credentials() -> None:
    """An explicit SSO profile cannot be redirected by ambient credentials."""
    creds, method = resolve_credentials(
        access_key_id="ambient-key",
        secret_access_key="ambient-secret",
        auth_mode="sso",
    )
    assert creds is None
    assert method == "sso"


def test_resolve_credentials_forced_static_fails_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Static mode fails closed instead of falling through to browser auth."""
    monkeypatch.setenv(ENV_AWS_SHARED_CREDENTIALS_FILE, "/nonexistent/credentials")
    with pytest.raises(RuntimeError, match="Static object-storage credentials"):
        resolve_credentials(auth_mode="static")


def test_resolve_credentials_rejects_unknown_mode() -> None:
    """Invalid runtime values are rejected even when callers bypass typing."""
    with pytest.raises(ValueError, match="Unknown authentication mode"):
        resolve_credentials(auth_mode="invalid")  # type: ignore[arg-type]
