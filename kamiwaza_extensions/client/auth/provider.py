"""Credential provider — yields raw credentials for any adapter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.credentials import RefreshableCredentials  # type: ignore[import-untyped]

from kamiwaza_extensions.client.auth.chain import AuthMode, resolve_credentials
from kamiwaza_extensions.client.auth.credentials import Credentials
from kamiwaza_extensions.client.auth.sso import (
    exchange_token_for_credentials,
    get_cloudflare_token,
)
from kamiwaza_extensions.client.auth.static import StaticCredentials
from kamiwaza_extensions.client.config import Config
from kamiwaza_extensions.client.options import ExplicitCredentials

# Refresh R2 creds when they have less than this many seconds left
_REFRESH_BUFFER_SECONDS = 60


class CredentialProvider:
    """Provides raw credentials for any adapter (boto3, rclone, aws-cli, etc.).

    Handles both static and SSO flows. Adapters call get_credentials() and
    receive Credentials in a format they can consume.

    When bucket is set, SSO credentials are requested for that bucket.
    Useful when the user is in multiple groups and the default role-based
    bucket would be prod; pass bucket='dev-kevin-test' to get dev creds.
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        explicit: ExplicitCredentials | None = None,
        bucket: str | None = None,
        auth_mode: AuthMode = "auto",
    ) -> None:
        self._config = config or Config.load()
        self._explicit = explicit or ExplicitCredentials()
        self._bucket = bucket
        self._auth_mode = auth_mode
        self._sso_cache: dict[str, Credentials] = {}  # keyed by bucket or "default"

    def resolve_static(self) -> tuple[StaticCredentials | None, str]:
        """Resolve the caller-supplied credentials against the auth mode."""
        return resolve_credentials(
            access_key_id=self._explicit.access_key_id,
            secret_access_key=self._explicit.secret_access_key,
            session_token=self._explicit.session_token,
            auth_mode=self._auth_mode,
        )

    def get_credentials(self) -> Credentials:
        """Return credentials (static or SSO). Handles refresh for SSO."""
        static, method = self.resolve_static()

        if method == "static" and static is not None:
            return self._static_to_credentials(static)

        return self._get_sso_credentials()

    def get_credentials_for_bucket(self, bucket: str) -> Credentials:
        """Return credentials for a specific bucket (SSO only).

        Use when managing multiple buckets: fetches or returns cached creds
        for the given bucket. Browser login happens once (JWT cached); per-bucket
        credential fetches use the cached JWT.

        Raises:
            RuntimeError: When using static credentials (bucket-specific not supported).
        """
        static, method = self.resolve_static()
        if method == "static" and static is not None:
            raise RuntimeError(
                "get_credentials_for_bucket is only for SSO flow. "
                "With static credentials, use a single client."
            )
        return self._get_sso_credentials_for_bucket(bucket)

    def _static_to_credentials(self, static: StaticCredentials) -> Credentials:
        """Convert static credentials to Credentials."""
        return Credentials(
            access_key_id=static.access_key_id,
            secret_access_key=static.secret_access_key,
            session_token=static.session_token,
            expiry=None,
        )

    def _get_sso_credentials(self) -> Credentials:
        """Get SSO credentials (cached or fresh from broker)."""
        cache_key = self._bucket or "default"
        return self._get_sso_credentials_for_bucket(cache_key)

    def _get_sso_credentials_for_bucket(self, bucket_or_default: str) -> Credentials:
        """Get SSO credentials for a bucket (cached or fresh from broker)."""
        cache_key = bucket_or_default
        now = datetime.now(timezone.utc)
        cached = self._cached_if_fresh(cache_key, now)
        if cached is not None:
            return cached

        broker_url = self._config.r2.broker_url
        if not broker_url:
            raise RuntimeError(
                "Broker URL not configured. Set R2_BROKER_URL in your environment."
            )

        bucket_param = None if cache_key == "default" else cache_key
        token = get_cloudflare_token(self._config, bucket=bucket_param)
        result = exchange_token_for_credentials(token, broker_url, bucket=bucket_param)
        creds = Credentials(
            access_key_id=result["access_key_id"],
            secret_access_key=result["secret_access_key"],
            session_token=result.get("session_token"),
            expiry=self._resolve_expiry(result.get("expiration"), now),
        )
        self._sso_cache[cache_key] = creds
        return creds

    def _cached_if_fresh(self, cache_key: str, now: datetime) -> Credentials | None:
        """Return the cached credentials while they remain outside the buffer."""
        cached = self._sso_cache.get(cache_key)
        if not cached or not cached.expiry:
            return None
        buffer = timedelta(seconds=_REFRESH_BUFFER_SECONDS)
        return cached if cached.expiry > now + buffer else None

    def _resolve_expiry(self, expiration: Any, now: datetime) -> datetime:
        """Validate the broker expiry, falling back to the configured TTL."""
        expiry = _parse_expiry(expiration)
        if expiration is not None and expiry is None:
            raise RuntimeError("Credential broker returned an invalid expiration")
        if expiry is None:
            expiry = now + timedelta(
                seconds=self._config.defaults.credential_ttl_seconds
            )
        if expiry <= now:
            raise RuntimeError("Credential broker returned expired credentials")
        return expiry

    def to_refreshable_credentials(
        self, bucket: str | None = None
    ) -> RefreshableCredentials:
        """Wrap temporary credentials for botocore automatic refresh."""

        def get_current() -> Credentials:
            if bucket is None:
                return self.get_credentials()
            return self.get_credentials_for_bucket(bucket)

        creds = get_current()
        if creds.expiry is None:
            raise RuntimeError("Static credentials cannot be made refreshable")

        def refresh() -> dict[str, Any]:
            c = get_current()
            if c.expiry is None:
                raise RuntimeError("Credential broker returned no expiration")
            return {
                "access_key": c.access_key_id,
                "secret_key": c.secret_access_key,
                "token": c.session_token,
                "expiry_time": c.expiry.isoformat(),
            }

        return RefreshableCredentials(
            access_key=creds.access_key_id,
            secret_key=creds.secret_access_key,
            token=creds.session_token,
            expiry_time=creds.expiry,
            refresh_using=refresh,
            method="r2-sso",
            advisory_timeout=_REFRESH_BUFFER_SECONDS,
            mandatory_timeout=10,
        )


def _parse_expiry(value: str | None) -> datetime | None:
    """Parse ISO expiration string to datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, TypeError):
        return None
