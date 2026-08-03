"""Static credential provider (explicit + AWS credentials file + environment).

Priority 1: Explicit credentials passed to constructor
Priority 2: ~/.aws/credentials (profile from AWS_PROFILE, default "default")
Priority 3: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from environment
Priority 4: R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY from environment
"""

from __future__ import annotations

import os
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path

from kamiwaza_extensions.client.config import ENV_ACCESS_KEY_ID, ENV_SECRET_ACCESS_KEY

# AWS credential file location (AWS_SHARED_CREDENTIALS_FILE or ~/.aws/credentials)
ENV_AWS_ACCESS_KEY_ID = "AWS_ACCESS_KEY_ID"
ENV_AWS_SECRET_ACCESS_KEY = "AWS_SECRET_ACCESS_KEY"
ENV_AWS_SESSION_TOKEN = "AWS_SESSION_TOKEN"
ENV_AWS_PROFILE = "AWS_PROFILE"
ENV_AWS_SHARED_CREDENTIALS_FILE = "AWS_SHARED_CREDENTIALS_FILE"

DEFAULT_AWS_CREDENTIALS_PATH = Path.home() / ".aws" / "credentials"


@dataclass
class StaticCredentials:
    """Static (non-refreshable) credentials."""

    access_key_id: str
    secret_access_key: str
    session_token: str | None = None
    expiry: None = None  # Static creds don't expire


def _load_aws_credentials_file(profile: str) -> StaticCredentials | None:
    """Load credentials from ~/.aws/credentials for the given profile."""
    path = Path(
        os.environ.get(
            ENV_AWS_SHARED_CREDENTIALS_FILE, str(DEFAULT_AWS_CREDENTIALS_PATH)
        )
    ).expanduser()
    if not path.exists():
        return None
    try:
        parser = ConfigParser()
        parser.read(path)
        if profile not in parser:
            return None
        section = parser[profile]
        access_key = section.get("aws_access_key_id", "").strip()
        secret_key = section.get("aws_secret_access_key", "").strip()
        if not access_key or not secret_key:
            return None
        session_token = section.get("aws_session_token", "").strip() or None
        return StaticCredentials(
            access_key_id=access_key,
            secret_access_key=secret_key,
            session_token=session_token,
        )
    except (OSError, KeyError):
        return None


def _validate_explicit(
    access_key_id: str | None,
    secret_access_key: str | None,
    session_token: str | None,
) -> None:
    """Reject a partially supplied explicit credential triple."""
    if bool(access_key_id) != bool(secret_access_key):
        raise ValueError(
            "access_key_id and secret_access_key must be provided together"
        )
    if session_token and not access_key_id:
        raise ValueError("session_token requires explicit access and secret keys")


def _from_env_pair(
    access_var: str, secret_var: str, token_var: str
) -> StaticCredentials | None:
    """Build credentials from a matched environment variable pair."""
    access = os.environ.get(access_var)
    secret = os.environ.get(secret_var)
    if not access or not secret:
        return None
    return StaticCredentials(
        access_key_id=access,
        secret_access_key=secret,
        session_token=os.environ.get(token_var),
    )


def get_static_credentials(
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    session_token: str | None = None,
) -> StaticCredentials | None:
    """Resolve static credentials from explicit args, environment, or ~/.aws/credentials.

    Sources are tried in the documented priority order and the first that
    yields a complete pair wins. Returns None if no static credentials are
    available.
    """
    _validate_explicit(access_key_id, secret_access_key, session_token)

    if access_key_id and secret_access_key:
        return StaticCredentials(
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

    sources = (
        lambda: _load_aws_credentials_file(
            os.environ.get(ENV_AWS_PROFILE, "default")
        ),
        lambda: _from_env_pair(
            ENV_AWS_ACCESS_KEY_ID, ENV_AWS_SECRET_ACCESS_KEY, ENV_AWS_SESSION_TOKEN
        ),
        lambda: _from_env_pair(
            ENV_ACCESS_KEY_ID, ENV_SECRET_ACCESS_KEY, "R2_SESSION_TOKEN"
        ),
    )
    for source in sources:
        creds = source()
        if creds is not None:
            return creds
    return None


class StaticCredentialProvider:
    """Provides static credentials for pass-through to boto3."""

    def __init__(
        self,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
    ) -> None:
        self._creds = get_static_credentials(
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

    def can_provide(self) -> bool:
        """Check if static credentials are available."""
        return self._creds is not None

    def get_credentials(self) -> StaticCredentials | None:
        """Get static credentials if available."""
        return self._creds
