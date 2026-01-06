#!/usr/bin/env python3
import argparse
import sys
import uuid
from uuid import UUID

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import ApiKeyAuthenticator
from kamiwaza_sdk.schemas.auth import LocalUserCreateRequest, LocalUserPasswordResetRequest


def main() -> int:
    ap = argparse.ArgumentParser(description="Auth-on user-create/reset smoke via SDK")
    ap.add_argument("--base-url", required=True, help="https://<host> (NO /auth suffix)")
    ap.add_argument("--admin-user", required=True)
    ap.add_argument("--admin-pass", required=True)
    ap.add_argument("--client-id", default="kamiwaza-platform")
    ap.add_argument("--client-secret", default=None)
    ap.add_argument("--verify-ssl", default="true", choices=["true", "false"])
    ap.add_argument("--roles", default=None, help="comma-separated roles; optional")
    args = ap.parse_args()

    verify_ssl = args.verify_ssl == "true"
    base_url = args.base_url.rstrip("/")
    if base_url.endswith("/auth"):
        raise ValueError("base_url must NOT include /auth (SDK appends it)")

    new_user = f"fed_smoke_{uuid.uuid4().hex[:8]}"
    roles = None
    if args.roles:
        roles = [r.strip() for r in args.roles.split(",") if r.strip()]

    stage = "admin_login"
    bootstrap = KamiwazaClient(base_url=base_url)
    bootstrap.session.verify = verify_ssl
    tokens = bootstrap.auth.login_with_password(
        args.admin_user,
        args.admin_pass,
        client_id=args.client_id,
        client_secret=args.client_secret,
    )

    stage = "create_local_user"
    admin = KamiwazaClient(base_url=base_url, authenticator=ApiKeyAuthenticator(tokens.access_token))
    admin.session.verify = verify_ssl
    created = admin.auth.create_local_user(
        LocalUserCreateRequest(username=new_user, password="Passw0rd!", roles=roles)
    )
    user_id = UUID(str(created.id))
    print(f"created user: {created.username} id={created.id}")

    stage = "new_user_login"
    bootstrap.auth.login_with_password(
        new_user,
        "Passw0rd!",
        client_id=args.client_id,
        client_secret=args.client_secret,
    )
    print("new user login: OK")

    stage = "reset_password"
    admin.auth.reset_user_password(user_id, LocalUserPasswordResetRequest(new_password="NewPassw0rd!"))

    stage = "new_user_login_after_reset"
    bootstrap.auth.login_with_password(
        new_user,
        "NewPassw0rd!",
        client_id=args.client_id,
        client_secret=args.client_secret,
    )
    print("password reset + login: OK")

    print("PASS: create -> auth -> reset -> auth")
    return 0


if __name__ == "__main__":
    stage = "startup"
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL at stage={stage}: {exc}", file=sys.stderr)
        raise
