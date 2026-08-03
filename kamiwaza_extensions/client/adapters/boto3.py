"""boto3 S3 client adapter — primary adapter for R2."""

from __future__ import annotations

from typing import Any, cast

import boto3  # type: ignore[import-untyped]
import botocore.session  # type: ignore[import-untyped]

from kamiwaza_extensions.client.auth.credentials import Credentials
from kamiwaza_extensions.client.auth.provider import CredentialProvider
from kamiwaza_extensions.client.auth.static import StaticCredentials
from kamiwaza_extensions.client.config import Config
from kamiwaza_extensions.client.options import ClientOptions, ExplicitCredentials


def _s3_kwargs(endpoint: str | None, region: str) -> dict[str, Any]:
    """Base kwargs shared by every boto3 S3 client and resource we build."""
    kwargs: dict[str, Any] = {"service_name": "s3", "region_name": region}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return kwargs


def _refreshable_session(
    provider: CredentialProvider, bucket: str | None = None
) -> boto3.Session:
    """Create a boto3 session backed by refreshable broker credentials."""
    core_session = botocore.session.get_session()
    core_session._credentials = provider.to_refreshable_credentials(bucket)  # type: ignore[attr-defined]
    return boto3.Session(botocore_session=core_session)


def _extract_bucket_from_kwargs(_operation: str, kwargs: dict[str, Any]) -> str | None:
    """Extract bucket name from S3 operation kwargs.

    Most operations use 'Bucket'. copy_object uses 'Bucket' for destination.
    list_buckets has no bucket. Returns None when bucket cannot be determined.
    """
    if "Bucket" in kwargs:
        return cast(str, kwargs["Bucket"])
    # CopySource can be 'bucket/key' or {'Bucket': 'x', 'Key': 'y'}
    copy_source = kwargs.get("CopySource")
    if isinstance(copy_source, dict) and "Bucket" in copy_source:
        return cast(str, copy_source["Bucket"])
    if isinstance(copy_source, str) and "/" in copy_source:
        return copy_source.split("/", 1)[0]
    return None


class _MultiBucketClient:
    """Proxy S3 client that lazily fetches credentials per bucket.

    Intercepts calls, extracts the bucket from kwargs, and delegates to a
    boto3 client created with credentials for that bucket. Browser login
    happens at most once (JWT cached); per-bucket credential fetches use
    the cached JWT.
    """

    def __init__(
        self,
        credential_provider: CredentialProvider,
        endpoint: str | None,
        region: str,
    ) -> None:
        self._provider = credential_provider
        self._endpoint = endpoint
        self._region = region
        self._clients: dict[str, Any] = {}

    def _get_client_for_bucket(self, bucket: str) -> Any:
        if bucket not in self._clients:
            session = _refreshable_session(self._provider, bucket)
            self._clients[bucket] = session.client(
                **_s3_kwargs(self._endpoint, self._region)
            )
        return self._clients[bucket]

    def _delegate(self, operation: str, kwargs: dict[str, Any]) -> Any:
        bucket = _extract_bucket_from_kwargs(operation, kwargs)
        if bucket is None:
            # list_buckets etc. - use default creds (broker with no bucket)
            bucket = "default"
        client = self._get_client_for_bucket(bucket)
        method = getattr(client, operation)
        return method(**kwargs)

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access. Operations become wrapped calls; meta/paginators use default client."""
        if name.startswith("_"):
            raise AttributeError(name)
        # meta, get_paginator, get_waiter - proxy to default client
        if name in ("meta", "get_paginator", "get_waiter"):
            return getattr(self._get_client_for_bucket("default"), name)

        def _wrapped(*_args: Any, **kwargs: Any) -> Any:
            return self._delegate(name, kwargs)

        return _wrapped


class Boto3Adapter:
    """Creates boto3 S3 client/resource for R2 with auth resolution.

    Implements the adapter pattern: consumes CredentialProvider and produces
    boto3 clients and resources.
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        options: ClientOptions | None = None,
        explicit: ExplicitCredentials | None = None,
    ) -> None:
        self._config = config or Config.load()
        self._options = options or ClientOptions()
        self._credential_provider = CredentialProvider(
            config=self._config,
            explicit=explicit,
            bucket=self._options.bucket,
            auth_mode=self._options.auth_mode,
        )

    def get_credentials(self) -> Credentials:
        """Return raw credentials (for other adapters or direct use)."""
        return self._credential_provider.get_credentials()

    def _connection(self) -> tuple[str | None, str]:
        """Resolve the endpoint and region this adapter will connect with."""
        endpoint = self._config.get_endpoint_url(self._options.endpoint_url)
        region = self._options.region_name or self._config.defaults.region
        return endpoint, region

    def get_client(self) -> Any:
        """Return boto3 S3 client or multi-bucket proxy.

        With static credentials: returns a real boto3 client.
        With SSO and no bucket: returns MultiBucketClient (lazily fetches creds per bucket).
        With SSO and bucket set: returns a single-bucket boto3 client.
        """
        static, method = self._credential_provider.resolve_static()
        endpoint, region = self._connection()

        if method == "static" and static is not None:
            return self._static_factory("client", static, endpoint, region)
        if method == "sso" and self._options.bucket is None:
            return _MultiBucketClient(
                credential_provider=self._credential_provider,
                endpoint=endpoint,
                region=region,
            )
        return self._sso_factory("client", endpoint, region)

    def get_resource(self) -> Any:
        """Return a boto3 S3 resource (not a wrapper)."""
        static, method = self._credential_provider.resolve_static()
        endpoint, region = self._connection()

        if method == "static" and static is not None:
            return self._static_factory("resource", static, endpoint, region)
        return self._sso_factory("resource", endpoint, region)

    @staticmethod
    def _static_factory(
        kind: str,
        creds: StaticCredentials,
        endpoint: str | None,
        region: str,
    ) -> Any:
        """Build a boto3 client or resource from static credentials."""
        kwargs = _s3_kwargs(endpoint, region)
        kwargs["aws_access_key_id"] = creds.access_key_id
        kwargs["aws_secret_access_key"] = creds.secret_access_key
        if creds.session_token:
            kwargs["aws_session_token"] = creds.session_token
        return getattr(boto3, kind)(**kwargs)

    def _sso_factory(self, kind: str, endpoint: str | None, region: str) -> Any:
        """Build a boto3 client or resource from broker temp credentials."""
        session = _refreshable_session(self._credential_provider)
        return getattr(session, kind)(**_s3_kwargs(endpoint, region))
