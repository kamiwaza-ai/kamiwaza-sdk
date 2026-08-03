"""Grouped inputs for object-storage client construction.

The client takes two kinds of input: where to connect and how to authenticate.
Keeping them as two objects rather than a flat keyword list means the adapter,
the credential provider, and the public entry points all pass the same shapes
around instead of re-threading six positional concerns each.
"""

from __future__ import annotations

from dataclasses import dataclass

from kamiwaza_extensions.client.auth.chain import AuthMode


@dataclass(frozen=True)
class ExplicitCredentials:
    """Caller-supplied static credentials.

    All three fields empty means "resolve from the environment"; supplying a
    key requires supplying its secret.
    """

    access_key_id: str | None = None
    secret_access_key: str | None = None
    session_token: str | None = None


@dataclass(frozen=True)
class ClientOptions:
    """Where to connect and which auth strategy to use.

    ``bucket`` is only meaningful for SSO: ``None`` yields a multi-bucket
    client that resolves credentials per bucket, a name yields a single-bucket
    client.
    """

    endpoint_url: str | None = None
    region_name: str | None = None
    bucket: str | None = None
    auth_mode: AuthMode = "auto"
