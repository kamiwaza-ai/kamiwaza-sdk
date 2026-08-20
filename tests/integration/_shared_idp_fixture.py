"""Stand up the shared_idp realm the gated-retrieval live tests need (ENG-8325).

``test_federation_shared_idp_gated_retrieval_live.py`` has a three-deep
precondition chain, and each guard only reports the FIRST unmet one — so the
suite reads "4 skipped" identically at every stage while meaning something
different each time:

1. the acme-gates wheel + pip index      -> ``_gate_fixture.py``
2. ``MINI_CLEARANCE_DATASET_PATH``       -> ``_gate_fixture.py`` publishes the CSV
3. a **shared realm** both clusters trust -> this module

The realm has to project ``clearance`` and an explicit
``tenant_id=__default__`` into brokered JWTs. The three clearance personas and
one deliberately unonboarded persona must mint tokens from it by ROPC, because
the receiver's
shared_idp validation accepts a caller only when the token's ``kid`` is in the
SHARED realm's JWKS. A valid shared-realm token is still receiver-denied until
its subject is explicitly onboarded; that allowlist boundary is part of the
suite's required proof.

This drives the primitives that already ship in
``kamiwaza_sdk.seeding.federation`` (the ``kamiwaza-federation idp`` group) rather than
re-implementing Keycloak admin calls. It is DEV/TEST only: it needs master-realm
admin, which the ingress deliberately does not expose, so it reaches Keycloak
through a port-forward. Production provisioning belongs in the auth chart's
init-Job pipeline (ENG-8573).

Usage::

    export SHARED_REALM_NAME=kajiya-edge-<unique-run-id>
    export SHARED_REALM_OWNER_NONCE=<random-per-run-nonce>
    export FED_PERSONA_PASSWORD=<random-per-run-password>
    python -m tests.integration._shared_idp_fixture provision --kubectl kubectl
    python -m tests.integration._shared_idp_fixture teardown --kubectl kubectl
    python -m tests.integration._shared_idp_fixture env

``provision`` refuses an existing realm before changing its profile, clients,
mappers, or users. ``teardown`` deletes the full realm only when its ownership
marker exactly matches ``SHARED_REALM_OWNER_NONCE``; an absent realm is an
idempotent success so callers can retry an ambiguous remote outcome safely.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import shlex
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Iterator

NAMESPACE = "kamiwaza"
ROPC_CLIENT = "kamiwaza-shared-cli"
DEFAULT_TENANT_ID = "__default__"
# Verified against the live chart: svc/keycloak exposes 80 (http) and 9000
# (management), NOT 8080. Overridable for a chart that differs.
KEYCLOAK_SVC_PORT = os.getenv("KEYCLOAK_SVC_PORT", "80")
# These names are a CONTRACT with the consumer, not a local choice: the live
# test passes every value straight to ROPC as the username, so a missing user
# is a module-fixture ERROR, not a skip. Keep both constants aligned there.
# Clearance values match _mini_clearance.KNOWN: U sees 3 rows, S sees 4, TS sees all 5.
PERSONAS = {"U": "fed-clr-u", "S": "fed-clr-s", "TS": "fed-clr-ts"}
UNONBOARDED_PERSONA = "fed-clr-unonboarded"


@dataclass(frozen=True)
class OwnedRealm:
    name: str
    owner_nonce: str


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    if cmd and cmd[0] == "ssh" and len(cmd) > 2:
        cmd = cmd[:2] + [shlex.join(cmd[2:])]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def keycloak_admin_channel(kubectl: str) -> Iterator[str]:
    """Yield a base URL reaching Keycloak's admin API.

    A port-forward rather than the ingress: the master-realm admin endpoints are
    deliberately not exposed publicly, and this fixture is the one caller that
    legitimately needs them. Torn down on exit either way.
    """
    argv = shlex.split(kubectl)
    if argv[0] == "ssh":
        raise SystemExit(
            "a port-forward cannot be tunnelled through `ssh <host> kubectl`.\n"
            "Run this fixture ON the cluster host, or point --kubectl at a "
            "kubeconfig context that reaches it directly."
        )
    port = _free_port()
    proc = subprocess.Popen(
        argv
        + [
            "-n",
            NAMESPACE,
            "port-forward",
            "svc/keycloak",
            f"{port}:{KEYCLOAK_SVC_PORT}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            with contextlib.suppress(OSError):
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            time.sleep(1)
        else:
            raise SystemExit("port-forward to svc/keycloak never became ready")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)


def admin_password(kubectl: str) -> str:
    argv = shlex.split(kubectl)
    got = run(
        argv
        + [
            "-n",
            NAMESPACE,
            "get",
            "secret",
            "keycloak-admin",
            "-o",
            "jsonpath={.data.password}",
        ]
    )
    if got.returncode != 0 or not got.stdout.strip():
        raise SystemExit(
            "could not read the keycloak-admin secret; this fixture needs "
            "master-realm admin to create the shared realm."
        )
    import base64

    return base64.b64decode(got.stdout.strip()).decode()


def provision(
    kc_url: str,
    admin_pw: str,
    persona_pw: str,
    owned_realm: OwnedRealm,
) -> dict:
    """Realm + ROPC client + claim mappers + clearance and negative personas."""
    from kamiwaza_sdk.seeding.federation.cli import _verify_ssl
    from kamiwaza_sdk.seeding.federation.keycloak import KeycloakAdmin

    # Honour KAMIWAZA_VERIFY_SSL rather than hardcoding it off. This particular
    # channel is plain HTTP to a localhost port-forward, so TLS is not in play
    # here at all — but a hardcoded verify=False is the kind of thing that gets
    # copied into a call where it does matter.
    kc = KeycloakAdmin(
        kc_url, admin_user="admin", admin_password=admin_pw, verify=_verify_ssl()
    )
    realm = owned_realm.name
    kc.create_owned_realm(realm, owned_realm.owner_nonce)
    try:
        # Keycloak >=24 drops unrecognised user attributes unless the realm opts
        # in, which silently strips the fixture's clearance and tenant attributes.
        kc.set_unmanaged_attributes(realm)
        client = kc.ensure_ropc_client(realm, ROPC_CLIENT)
        kc.ensure_attribute_mapper(realm, client["id"], attribute="clearance")
        kc.ensure_attribute_mapper(realm, client["id"], attribute="tenant_id")

        for clearance, username in PERSONAS.items():
            kc.ensure_user(
                realm,
                username,
                password=persona_pw,
                attributes={
                    "clearance": clearance,
                    "tenant_id": DEFAULT_TENANT_ID,
                },
            )
        kc.ensure_user(
            realm,
            UNONBOARDED_PERSONA,
            password=persona_pw,
            attributes={"clearance": "U", "tenant_id": DEFAULT_TENANT_ID},
        )
    except BaseException:
        try:
            kc.delete_owned_realm(realm, owned_realm.owner_nonce)
        except Exception as cleanup_error:
            raise RuntimeError(
                "shared realm provision failed and owned-realm rollback also failed"
            ) from cleanup_error
        raise

    issuer = kc.issuer_url(realm)
    return {
        "SHARED_ISSUER_URL": issuer,
        "SHARED_JWKS_URL": f"{issuer}/protocol/openid-connect/certs",
        "SHARED_REALM_CLIENT_ID": ROPC_CLIENT,
        "FED_PERSONA_PASSWORD": persona_pw,
    }


def teardown(kc_url: str, admin_pw: str, *, realm: str, owner_nonce: str) -> bool:
    """Delete the whole realm only when this run's ownership marker matches."""
    from kamiwaza_sdk.seeding.federation.cli import _verify_ssl
    from kamiwaza_sdk.seeding.federation.keycloak import KeycloakAdmin

    kc = KeycloakAdmin(
        kc_url, admin_user="admin", admin_password=admin_pw, verify=_verify_ssl()
    )
    return kc.delete_owned_realm(realm, owner_nonce)


def _required_owned_realm() -> OwnedRealm:
    realm = os.getenv("SHARED_REALM_NAME", "").strip()
    owner_nonce = os.getenv("SHARED_REALM_OWNER_NONCE", "").strip()
    if realm and owner_nonce:
        return OwnedRealm(realm, owner_nonce)
    raise SystemExit(
        "SHARED_REALM_NAME and SHARED_REALM_OWNER_NONCE are required; "
        "the fixture refuses fixed or unowned realms"
    )


def _run_fixture_action(args: argparse.Namespace, owned_realm: OwnedRealm) -> int:
    admin_pw = admin_password(args.kubectl)
    if args.action == "teardown":
        with keycloak_admin_channel(args.kubectl) as kc_url:
            deleted = teardown(
                kc_url,
                admin_pw,
                realm=owned_realm.name,
                owner_nonce=owned_realm.owner_nonce,
            )
        status = "deleted" if deleted else "already-absent"
        print(f"  realm={owned_realm.name} cleanup={status}")
        return 0

    persona_pw = os.getenv(
        args.persona_password_env, ""
    ).strip() or secrets.token_urlsafe(24)
    with keycloak_admin_channel(args.kubectl) as kc_url:
        exports = provision(kc_url, admin_pw, persona_pw, owned_realm)
    _print_provisioned_realm(owned_realm.name, exports)
    return 0


def _print_provisioned_realm(realm: str, exports: dict[str, str]) -> None:
    public = os.getenv("KEYCLOAK_PUBLIC_URL", "").strip()
    if public:
        exports["SHARED_ISSUER_URL"] = f"{public}/realms/{realm}"
        exports["SHARED_JWKS_URL"] = (
            f"{public}/realms/{realm}/protocol/openid-connect/certs"
        )
    else:
        print(
            "  WARNING: issuer points at the port-forward. Set KEYCLOAK_PUBLIC_URL\n"
            "  to the address BOTH clusters resolve, or the receiver cannot fetch\n"
            "  the shared JWKS and every persona token is rejected."
        )

    usernames = [*PERSONAS.values(), UNONBOARDED_PERSONA]
    print(f"  realm={realm} client={ROPC_CLIENT} personas={sorted(usernames)}")
    print(json.dumps(exports, indent=2))
    for key, value in exports.items():
        print(f"export {key}={shlex.quote(value)}")
    print(
        "\n  NEXT (required, or the suite goes red with 403 "
        "shared_idp_issuer_untrusted):\n"
        "  enroll this issuer on the RECEIVER, then resync:\n"
        "    scheduler:\n"
        "      trustedSharedIssuers:\n"
        f"        - {exports['SHARED_ISSUER_URL']}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["provision", "teardown", "env"])
    ap.add_argument("--kubectl", default="kubectl")
    ap.add_argument(
        "--persona-password-env",
        default="FED_PERSONA_PASSWORD",
        help="env var holding the persona password; generated when unset",
    )
    args = ap.parse_args()

    if args.action == "env":
        print("# provision first; it prints the exports")
        return 0
    return _run_fixture_action(args, _required_owned_realm())


if __name__ == "__main__":
    raise SystemExit(main())
