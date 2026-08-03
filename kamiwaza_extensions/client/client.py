"""Public API for the S3-compatible object-storage client."""

from __future__ import annotations

from typing import Any

from kamiwaza_extensions.client.adapters.boto3 import Boto3Adapter
from kamiwaza_extensions.client.auth.chain import AuthMode
from kamiwaza_extensions.client.config import Config


def get_client(
    *,
    endpoint_url: str | None = None,
    region_name: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    session_token: str | None = None,
    bucket: str | None = None,
    config: Config | None = None,
    auth_mode: AuthMode = "auto",
) -> Any:
    """Get a boto3 S3 client for Cloudflare R2.

    This is the SDK's boto3-compatible entry point. With SSO and no bucket, it
    returns a multi-bucket client that lazily fetches credentials per bucket.

    Auth resolution (in order):
      1. Explicit access_key_id + secret_access_key (no broker call)
      2. ~/.aws/credentials (profile from AWS_PROFILE, default "default")
      3. AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY from env (no broker call)
      4. R2_ACCESS_KEY_ID + R2_SECRET_ACCESS_KEY from env (no broker call)
      5. Cloudflare auth via credential broker (browser login if needed)

    Multi-bucket mode (SSO, bucket=None):
      Use one client for multiple buckets. Credentials are fetched on first
      access to each bucket. Browser login happens at most once (JWT cached);
      per-bucket credential fetches use the cached JWT.

      Example:
        s3 = get_client()
        s3.list_objects_v2(Bucket='dev-kevin-test', MaxKeys=5)   # dev creds
        s3.put_object(Bucket='stage-kevin-test', Key='x', Body=b'')  # stage creds

    Single-bucket mode (SSO, bucket='dev-kevin-test'):
      Returns a real boto3 client with credentials for that bucket. Use when
      you need get_paginator or other features that require a fixed client.

    Args:
        endpoint_url: R2 endpoint (default from config/env)
        region_name: Region (default "auto" for R2)
        access_key_id: Explicit access key (skips SSO)
        secret_access_key: Explicit secret key (skips SSO)
        session_token: Optional session token for temp creds
        bucket: When using SSO: None = multi-bucket client; str = single-bucket client.
        config: Optional config override (default: load from file/env)
        auth_mode: ``auto`` selects static credentials when available and SSO
            otherwise; ``static`` or ``sso`` selects a strategy explicitly.

    Returns:
        boto3 S3 client or multi-bucket proxy
    """
    adapter = Boto3Adapter(
        config=config,
        endpoint_url=endpoint_url,
        region_name=region_name,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
        bucket=bucket,
        auth_mode=auth_mode,
    )
    return adapter.get_client()


def get_resource(
    *,
    endpoint_url: str | None = None,
    region_name: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    session_token: str | None = None,
    bucket: str | None = None,
    config: Config | None = None,
    auth_mode: AuthMode = "auto",
) -> Any:
    """Get a boto3 S3 resource for Cloudflare R2.

    Same auth resolution as get_client(). Returns a real boto3 S3 resource.

    Args:
        endpoint_url: R2 endpoint (default from config/env)
        region_name: Region (default "auto" for R2)
        access_key_id: Explicit access key (skips SSO)
        secret_access_key: Explicit secret key (skips SSO)
        session_token: Optional session token for temp creds
        bucket: When using SSO, request credentials for this bucket (see get_client)
        config: Optional config override
        auth_mode: ``auto`` selects static credentials when available and SSO
            otherwise; ``static`` or ``sso`` selects a strategy explicitly.

    Returns:
        boto3 S3 resource instance
    """
    adapter = Boto3Adapter(
        config=config,
        endpoint_url=endpoint_url,
        region_name=region_name,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
        bucket=bucket,
        auth_mode=auth_mode,
    )
    return adapter.get_resource()
