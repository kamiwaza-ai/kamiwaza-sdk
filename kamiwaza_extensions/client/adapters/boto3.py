"""boto3 S3 client adapter — primary adapter for R2."""

from __future__ import annotations

from typing import Any, cast

import boto3  # type: ignore[import-untyped]
import botocore.session  # type: ignore[import-untyped]

from kamiwaza_extensions.client.auth.chain import AuthMode, resolve_credentials
from kamiwaza_extensions.client.auth.credentials import Credentials
from kamiwaza_extensions.client.auth.provider import CredentialProvider
from kamiwaza_extensions.client.auth.static import StaticCredentials
from kamiwaza_extensions.client.config import Config


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
            client_kwargs: dict[str, Any] = {
                "service_name": "s3",
                "region_name": self._region,
            }
            if self._endpoint:
                client_kwargs["endpoint_url"] = self._endpoint
            session = _refreshable_session(self._provider, bucket)
            self._clients[bucket] = session.client(**client_kwargs)
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
        endpoint_url: str | None = None,
        region_name: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
        bucket: str | None = None,
        auth_mode: AuthMode = "auto",
    ) -> None:
        self._config = config or Config.load()
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._session_token = session_token
        self._bucket = bucket
        self._credential_provider = CredentialProvider(
            config=self._config,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
            bucket=bucket,
            auth_mode=auth_mode,
        )
        self._endpoint_url = endpoint_url
        self._region_name = region_name
        self._auth_mode = auth_mode

    def get_credentials(self) -> Credentials:
        """Return raw credentials (for other adapters or direct use)."""
        return self._credential_provider.get_credentials()

    def get_client(self) -> Any:
        """Return boto3 S3 client or multi-bucket proxy.

        With static credentials: returns a real boto3 client.
        With SSO and no bucket: returns MultiBucketClient (lazily fetches creds per bucket).
        With SSO and bucket set: returns a single-bucket boto3 client.
        """
        static, method = resolve_credentials(
            access_key_id=self._access_key_id,
            secret_access_key=self._secret_access_key,
            session_token=self._session_token,
            auth_mode=self._auth_mode,
        )

        endpoint = self._config.get_endpoint_url(self._endpoint_url)
        region = self._region_name or self._config.defaults.region

        if method == "static" and static is not None:
            return self._create_static_client(static, endpoint, region)
        if method == "sso" and self._bucket is None:
            return _MultiBucketClient(
                credential_provider=self._credential_provider,
                endpoint=endpoint,
                region=region,
            )
        return self._create_sso_client(endpoint, region)

    def _create_static_client(
        self,
        creds: StaticCredentials,
        endpoint: str | None,
        region: str,
    ) -> Any:
        """Create boto3 client with static credentials."""
        client_kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": region,
            "aws_access_key_id": creds.access_key_id,
            "aws_secret_access_key": creds.secret_access_key,
        }
        if creds.session_token:
            client_kwargs["aws_session_token"] = creds.session_token
        if endpoint:
            client_kwargs["endpoint_url"] = endpoint

        return boto3.client(**client_kwargs)

    def _create_sso_client(self, endpoint: str | None, region: str) -> Any:
        """Create boto3 client with broker temp credentials (R2 direct)."""
        client_kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": region,
        }
        if endpoint:
            client_kwargs["endpoint_url"] = endpoint
        session = _refreshable_session(self._credential_provider)
        return session.client(**client_kwargs)

    def get_resource(self) -> Any:
        """Return a boto3 S3 resource (not a wrapper)."""
        static, method = resolve_credentials(
            access_key_id=self._access_key_id,
            secret_access_key=self._secret_access_key,
            session_token=self._session_token,
            auth_mode=self._auth_mode,
        )

        endpoint = self._config.get_endpoint_url(self._endpoint_url)
        region = self._region_name or self._config.defaults.region

        if method == "static" and static is not None:
            return self._create_static_resource(static, endpoint, region)
        return self._create_sso_resource(endpoint, region)

    def _create_static_resource(
        self,
        creds: StaticCredentials,
        endpoint: str | None,
        region: str,
    ) -> Any:
        """Create boto3 resource with static credentials."""
        resource_kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": region,
            "aws_access_key_id": creds.access_key_id,
            "aws_secret_access_key": creds.secret_access_key,
        }
        if creds.session_token:
            resource_kwargs["aws_session_token"] = creds.session_token
        if endpoint:
            resource_kwargs["endpoint_url"] = endpoint

        return boto3.resource(**resource_kwargs)

    def _create_sso_resource(self, endpoint: str | None, region: str) -> Any:
        """Create boto3 resource with broker temp credentials (R2 direct)."""
        resource_kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": region,
        }
        if endpoint:
            resource_kwargs["endpoint_url"] = endpoint
        session = _refreshable_session(self._credential_provider)
        return session.resource(**resource_kwargs)
