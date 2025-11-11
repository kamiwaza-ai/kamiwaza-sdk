"""Kamiwaza SDK command-line helpers.

Example usage (doctest-friendly):

>>> from kamiwaza_sdk.cli import build_parser
>>> parser = build_parser()
>>> parsed = parser.parse_args(["login", "--username", "demo", "--password", "secret"])
>>> parsed.command
'login'
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Callable, Optional

from .authentication import UserPasswordAuthenticator
from .client import KamiwazaClient
from .exceptions import AuthenticationError
from .schemas.auth import PATCreate
from .token_store import FileTokenStore, StoredToken, TokenStore

DEFAULT_BASE_URL = os.environ.get("KAMIWAZA_BASE_URL", "https://localhost/api")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kamiwaza SDK utilities")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Kamiwaza API base URL (default: %(default)s)")
    parser.add_argument(
        "--token-path",
        default=os.environ.get("KAMIWAZA_TOKEN_PATH"),
        help="Path to cached token file (default: ~/.kamiwaza/token.json)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="Perform username/password login and cache the session token")
    login.add_argument("--username", required=True, help="Username")
    login.add_argument("--password", required=True, help="Password")

    pat = sub.add_parser("pat", help="Manage personal access tokens")
    pat_sub = pat.add_subparsers(dest="pat_command", required=True)

    pat_create = pat_sub.add_parser("create", help="Create a new PAT")
    pat_create.add_argument("--name", required=True, help="Display name for the PAT")
    pat_create.add_argument("--ttl", type=int, default=3600, help="TTL in seconds (default: 3600)")
    pat_create.add_argument("--scope", default="openid", help="PAT scope (default: openid)")
    pat_create.add_argument(
        "--aud",
        default="kamiwaza-platform",
        help="Audience for the PAT (default: kamiwaza-platform)",
    )
    pat_create.add_argument(
        "--cache-token",
        action="store_true",
        help="Write the newly minted PAT token into the token cache file",
    )
    pat_create.add_argument(
        "--revoke-jti",
        help="Optional JTI of an older PAT to revoke after creating the new one",
    )

    return parser


def _default_client_factory(base_url: str, **kwargs) -> KamiwazaClient:
    return KamiwazaClient(base_url, **kwargs)


def login_command(
    args: argparse.Namespace,
    *,
    client_factory: Callable[..., KamiwazaClient] = _default_client_factory,
    token_store: Optional[TokenStore] = None,
    authenticator_cls=UserPasswordAuthenticator,
) -> str:
    store = token_store or FileTokenStore(args.token_path)
    client = client_factory(args.base_url)
    authenticator = authenticator_cls(
        args.username,
        args.password,
        client.auth,
        token_store=store,
    )
    authenticator.authenticate(client.session)
    return str(store.path if hasattr(store, "path") else args.token_path)


def pat_create_command(
    args: argparse.Namespace,
    *,
    client_factory: Callable[..., KamiwazaClient] = _default_client_factory,
    token_store: Optional[TokenStore] = None,
) -> str:
    store = token_store or FileTokenStore(args.token_path)
    cached = store.load()
    if not cached or cached.is_expired:
        raise AuthenticationError("Login first with `kamiwaza login` to cache a session token.")

    client = client_factory(args.base_url, api_key=cached.access_token)
    payload = PATCreate(
        name=args.name,
        ttl_seconds=args.ttl,
        scope=args.scope,
        aud=args.aud,
    )
    response = client.auth.create_pat(payload)
    if args.revoke_jti:
        client.auth.revoke_pat(args.revoke_jti)
    if args.cache_token:
        expires_at = float(response.pat.exp) if response.pat.exp else time.time() + (args.ttl or 0)
        store.save(StoredToken(access_token=response.token, refresh_token=None, expires_at=expires_at))
    return response.token


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "login":
        login_command(args)
        print("Login succeeded; token cached.")
        return 0
    if args.command == "pat" and args.pat_command == "create":
        token = pat_create_command(args)
        print(token)
        return 0
    parser.error("Unknown command")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
