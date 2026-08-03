"""Canonical credentials type for adapters.

All adapters (boto3, rclone, aws-cli, etc.) consume this format.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Credentials:
    """Raw credentials for S3-compatible storage.

    Adapters consume this and provide it in their native format
    (e.g., boto3 client, rclone config, env vars for aws cli).
    """

    access_key_id: str
    secret_access_key: str
    session_token: str | None = None
    expiry: datetime | None = None
