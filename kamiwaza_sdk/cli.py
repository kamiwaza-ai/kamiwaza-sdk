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
import json
import os
import time
from typing import Callable, Optional

from .authentication import UserPasswordAuthenticator
from .client import KamiwazaClient
from .exceptions import AuthenticationError
from .schemas.auth import PATCreate
from .token_store import FileTokenStore, StoredToken, TokenStore

DEFAULT_BASE_URL = (
    os.environ.get("KAMIWAZA_BASE_URL")
    or os.environ.get("KAMIWAZA_BASE_URI")
    or "https://localhost/api"
)


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

    serve = sub.add_parser("serve", help="Manage serving deployments")
    serve_sub = serve.add_subparsers(dest="serve_command", required=True)

    serve_deploy = serve_sub.add_parser("deploy", help="Deploy a model via the serving service")
    identity = serve_deploy.add_mutually_exclusive_group(required=True)
    identity.add_argument("--model-id", help="Model UUID to deploy")
    identity.add_argument("--repo-id", help="Hugging Face repo ID to deploy")
    serve_deploy.add_argument("--config-id", help="Model config UUID to deploy")
    serve_deploy.add_argument("--file-id", help="Specific model file UUID")
    serve_deploy.add_argument("--engine-name", help="Engine to use for deployment")
    serve_deploy.add_argument("--lb-port", type=int, default=0, help="Requested load balancer port (0 = auto)")
    serve_deploy.add_argument("--min-copies", type=int, default=1, help="Minimum number of copies")
    serve_deploy.add_argument("--starting-copies", type=int, default=1, help="Initial copies to launch")
    serve_deploy.add_argument("--max-copies", type=int, help="Maximum copies when autoscaling")
    serve_deploy.add_argument("--duration", type=int, help="Duration in minutes for the deployment")
    serve_deploy.add_argument("--autoscaling", action="store_true", help="Enable autoscaling")
    serve_deploy.add_argument("--force-cpu", action="store_true", help="Force CPU execution")
    serve_deploy.add_argument("--wait", action="store_true", help="Wait for deployment to reach DEPLOYED status")
    serve_deploy.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between status polls when waiting")
    serve_deploy.add_argument("--timeout", type=float, default=600.0, help="Timeout in seconds when waiting")

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


def serve_deploy_command(
    args: argparse.Namespace,
    *,
    client_factory: Callable[..., KamiwazaClient] = _default_client_factory,
    token_store: Optional[TokenStore] = None,
) -> dict[str, str]:
    store = token_store or FileTokenStore(args.token_path)
    cached = store.load()
    if not cached or cached.is_expired:
        raise AuthenticationError("Login first with `kamiwaza login` to cache a session token.")

    client = client_factory(args.base_url, api_key=cached.access_token)
    deployment_id = client.serving.deploy_model(
        model_id=args.model_id,
        repo_id=args.repo_id,
        m_config_id=args.config_id,
        m_file_id=args.file_id,
        engine_name=args.engine_name,
        lb_port=args.lb_port,
        min_copies=args.min_copies,
        starting_copies=args.starting_copies,
        max_copies=args.max_copies,
        duration=args.duration,
        autoscaling=args.autoscaling,
        force_cpu=args.force_cpu,
    )

    summary: dict[str, str] = {"deployment_id": str(deployment_id)}

    if args.wait:
        deployment = client.serving.wait_for_deployment(
            deployment_id,
            poll_interval=args.poll_interval,
            timeout=args.timeout,
        )
        summary["status"] = deployment.status
    return summary


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
    if args.command == "serve" and args.serve_command == "deploy":
        summary = serve_deploy_command(args)
        print(json.dumps(summary))
        return 0
    parser.error("Unknown command")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
