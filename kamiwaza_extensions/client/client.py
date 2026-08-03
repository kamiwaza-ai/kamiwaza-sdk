"""Public API for the S3-compatible object-storage client."""

from __future__ import annotations

from typing import Any

from kamiwaza_extensions.client.adapters.boto3 import Boto3Adapter
from kamiwaza_extensions.client.config import Config
from kamiwaza_extensions.client.options import ClientOptions, ExplicitCredentials



def get_client(
    *,
    options: ClientOptions | None = None,
    explicit: ExplicitCredentials | None = None,
    config: Config | None = None,
) -> Any:
    """Get a boto3 S3 client for Cloudflare R2.

    This is the SDK's boto3-compatible entry point. With SSO and no bucket, it
    returns a multi-bucket client that lazily fetches credentials per bucket.

    Auth resolution (in order):
      1. ``explicit`` access_key_id + secret_access_key (no broker call)
      2. ~/.aws/credentials (profile from AWS_PROFILE, default "default")
      3. AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY from env (no broker call)
      4. R2_ACCESS_KEY_ID + R2_SECRET_ACCESS_KEY from env (no broker call)
      5. Cloudflare auth via credential broker (browser login if needed)

    Multi-bucket mode (SSO, ``options.bucket`` is None):
      Use one client for multiple buckets. Credentials are fetched on first
      access to each bucket. Browser login happens at most once (JWT cached);
      per-bucket credential fetches use the cached JWT.

      Example:
        s3 = get_client()
        s3.list_objects_v2(Bucket='dev-kevin-test', MaxKeys=5)   # dev creds
        s3.put_object(Bucket='stage-kevin-test', Key='x', Body=b'')  # stage creds

    Single-bucket mode (SSO, ``options.bucket`` set):
      Returns a real boto3 client with credentials for that bucket. Use when
      you need get_paginator or other features that require a fixed client.

    Args:
        options: Endpoint, region, bucket, and auth mode. See ClientOptions.
        explicit: Caller-supplied static credentials, skipping SSO.
        config: Optional config override (default: load from file/env)

    Returns:
        boto3 S3 client or multi-bucket proxy
    """
    adapter = Boto3Adapter(config=config, options=options, explicit=explicit)
    return adapter.get_client()


def get_resource(
    *,
    options: ClientOptions | None = None,
    explicit: ExplicitCredentials | None = None,
    config: Config | None = None,
) -> Any:
    """Get a boto3 S3 resource for Cloudflare R2.

    Same auth resolution as get_client(). Returns a real boto3 S3 resource;
    there is no multi-bucket resource proxy, so ``options.bucket`` selects the
    bucket whose credentials are requested.

    Args:
        options: Endpoint, region, bucket, and auth mode. See ClientOptions.
        explicit: Caller-supplied static credentials, skipping SSO.
        config: Optional config override

    Returns:
        boto3 S3 resource instance
    """
    adapter = Boto3Adapter(config=config, options=options, explicit=explicit)
    return adapter.get_resource()
