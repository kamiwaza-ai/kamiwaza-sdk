"""``kamiwaza-federation`` — shared_idp seeding + ReBAC access CLI.

Deterministic, idempotent wrappers around SDK / Keycloak-admin methods. Secrets
are read from env vars (``--*-env``), never argv. Handlers return a dict (printed
as JSON) or None (self-output, e.g. ``idp token --raw``).

Groups::

    access  grant | revoke | list                 (ReBAC on resources)
    fed     pair | status | allow-user             (shared_idp lifecycle)
    dataset gated                                  (create a gate-bound dataset)
    gate    install                                (install a gate package)
    attr    declare                                (declare an attribute vocab)
    idp     bootstrap | persona | token            (Keycloak-side shared-realm)
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Optional

from ..cli import _read_env_secret
from ..client import build_client_from_env
from .keycloak import KeycloakAdmin, jwks_uri_from_issuer


def _verify_ssl() -> bool:
    """TLS verification for direct Keycloak calls (the SDK client honors this env
    natively; we mirror it for the idp group's raw Keycloak-admin calls)."""
    return os.environ.get("KAMIWAZA_VERIFY_SSL", "true").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _split_object(spec: str) -> tuple[str, str]:
    """Parse ``<namespace>:<id>`` — split on the FIRST colon only, since a
    resource id (e.g. a dataset URN) itself contains colons."""
    ns, sep, oid = spec.partition(":")
    if not sep or not ns or not oid:
        raise SystemExit(f"--object must be '<namespace>:<id>', got {spec!r}")
    return ns, oid


# --- backend construction (module-level so tests can monkeypatch) ----------


def build_kc_admin(args: argparse.Namespace) -> KeycloakAdmin:
    pw = _read_env_secret(args.kc_admin_pw_env, what="Keycloak admin password")
    if not pw:
        raise SystemExit("--kc-admin-pw-env must name an env var holding the password")
    return KeycloakAdmin(
        args.kc_url,
        admin_user=args.kc_admin_user,
        admin_password=pw,
        verify=_verify_ssl(),
    )


# --- access: ReBAC on resources -------------------------------------------


def cmd_access_grant(args: argparse.Namespace, *, client: Any) -> dict:
    ns, oid = _split_object(args.object)
    client.subjects.grants(args.subject).create(
        object_namespace=ns, object_id=oid, relation=args.relation
    )
    return {
        "granted": {
            "subject": args.subject,
            "relation": args.relation,
            "object": args.object,
        }
    }


def cmd_access_revoke(args: argparse.Namespace, *, client: Any) -> dict:
    ns, oid = _split_object(args.object)
    client.subjects.grants(args.subject).delete(
        object_namespace=ns, object_id=oid, relation=args.relation
    )
    return {
        "revoked": {
            "subject": args.subject,
            "relation": args.relation,
            "object": args.object,
        }
    }


def cmd_access_list(args: argparse.Namespace, *, client: Any) -> dict:
    grants = client.subjects.grants(args.subject).list()
    return {
        "subject": args.subject,
        "grants": [
            g.model_dump() if hasattr(g, "model_dump") else g for g in grants
        ],
    }


# --- fed: shared_idp federation lifecycle ---------------------------------


def cmd_fed_pair(args: argparse.Namespace, *, client: Any) -> dict:
    issuer = args.shared_issuer.rstrip("/")
    # H1-aligned: the JWKS is DERIVED from the issuer, never an arbitrary URL, so
    # the pairing binds the shared realm's keys to its issuer origin by construction.
    jwks = jwks_uri_from_issuer(issuer)
    ca = Path(args.shared_ca_file).read_text() if args.shared_ca_file else None
    admin_token = _read_env_secret(
        args.remote_admin_token_env, what="remote admin token"
    )
    # A two-sided shared_idp pairing needs the SAME preshared key on both
    # clusters — supply it via env so running this CLI on each side matches.
    # When omitted, pair() mints a fresh UUID4 (single-operator convenience;
    # only viable when the same value reaches both sides some other way).
    psk = _read_env_secret(args.preshared_key_env, what="preshared key")
    fed = client.federations.pair(
        name=args.name,
        role=args.role,
        remote_url=args.remote_url,
        preshared_key=psk,
        shared_issuer_url=issuer,
        shared_jwks_url=jwks,
        shared_ca_pem=ca,
        remote_admin_token=admin_token,
    )
    return {
        "paired": fed.model_dump() if hasattr(fed, "model_dump") else str(fed),
        "shared_issuer_url": issuer,
        "shared_jwks_url": jwks,
        "preshared_key_source": "env" if psk else "minted-uuid4",
    }


def cmd_fed_status(args: argparse.Namespace, *, client: Any) -> dict:
    items = [f.model_dump() for f in client.federations.list()]
    if args.name:
        items = [
            f
            for f in items
            if args.name in (f.get("remote_cluster_name"), str(f.get("id")))
        ]
    return {"federations": items}


def cmd_fed_allow_user(args: argparse.Namespace, *, client: Any) -> dict:
    # Build the receiver allowlist entry's initial ReBAC tuples from --seed
    # (repeatable) ``<namespace>:<id>:<relation>``. ``{{user_id}}`` renders to the
    # brokered principal's LOCAL uuid at receiver ingress.
    tuples = []
    for seed in args.seed or []:
        parts = seed.rsplit(":", 1)
        if len(parts) != 2 or ":" not in parts[0]:
            raise SystemExit(
                f"--seed must be '<namespace>:<id>:<relation>', got {seed!r}"
            )
        obj, relation = parts
        tuples.append(
            {"subject": "user:{{user_id}}", "relation": relation, "object": obj}
        )
    client._request(
        "POST",
        f"/cluster/federations/{args.federation}/users",
        json={"external_id": args.external_id, "initial_tuples": tuples},
    )
    return {
        "allowed": {
            "federation": args.federation,
            "external_id": args.external_id,
            "initial_tuples": tuples,
        }
    }


def cmd_fed_unpair(args: argparse.Namespace, *, client: Any) -> dict:
    result = client.federations[args.name].disconnect(force=args.force)
    return {"disconnected": args.name, "result": result}


# --- dataset / gate / attr: gated-retrieval setup -------------------------


def cmd_dataset_gated(args: argparse.Namespace, *, client: Any) -> dict:
    # Create the file dataset, THEN bind the gate via the dedicated endpoint
    # (set_gate -> PUT /catalog/datasets/{urn}/gate). Stuffing a "gate" property
    # into the catalog record does NOT enforce a gate; set_gate is what makes
    # retrieval gated (matches _mini_clearance.create_file_dataset).
    urn = client.datasets.create(
        name=args.name,
        platform="file",
        properties={"path": args.path},
        description=args.description,
    )
    config = json.loads(args.gate_config) if args.gate_config else {}
    client.datasets.set_gate(urn, type=args.gate, config=config)
    return {"dataset_urn": urn, "gate": args.gate, "path": args.path}


def cmd_gate_install(args: argparse.Namespace, *, client: Any) -> dict:
    result = client.gates.packages.install(
        package_spec=args.spec,
        hash_digest=args.hash,
        index_url=args.index_url,
    )
    return {
        "installed": result.model_dump()
        if hasattr(result, "model_dump")
        else str(result)
    }


def cmd_attr_declare(args: argparse.Namespace, *, client: Any) -> dict:
    client.cluster.declare_attribute(args.name, type=args.type)
    return {"declared": {"name": args.name, "type": args.type}}


# --- idp: Keycloak-side shared-realm seeding (dev) ------------------------


def cmd_idp_bootstrap(args: argparse.Namespace, *, client: Any = None) -> dict:
    kc = build_kc_admin(args)
    realm = kc.ensure_realm(args.realm)
    kc.set_unmanaged_attributes(args.realm)
    cli = kc.ensure_ropc_client(args.realm, args.ropc_client)
    for attr in args.attr or ["clearance"]:
        kc.ensure_attribute_mapper(args.realm, cli["id"], attribute=attr)
    return {
        "realm": realm,
        "ropc_client": cli,
        "attribute_mappers": args.attr or ["clearance"],
        "shared_issuer_url": kc.issuer_url(args.realm),
    }


def cmd_idp_persona(args: argparse.Namespace, *, client: Any = None) -> dict:
    kc = build_kc_admin(args)
    pw = _read_env_secret(args.pw_env, what="persona password")
    if not pw:
        raise SystemExit("--pw-env must name an env var holding the persona password")
    attributes = {}
    for pair in args.attr or []:
        k, sep, v = pair.partition("=")
        if not sep:
            raise SystemExit(f"--attr must be 'name=value', got {pair!r}")
        attributes[k] = v
    result = kc.ensure_user(
        args.realm, args.user, password=pw, attributes=attributes
    )
    return {"persona": result, "attributes": attributes}


def cmd_idp_token(args: argparse.Namespace, *, client: Any = None) -> Optional[dict]:
    # ROPC (direct access grant) needs only the persona creds + the public client
    # — no master-realm admin, so it does not use build_kc_admin.
    kc = KeycloakAdmin(
        args.kc_url, admin_user="", admin_password="", verify=_verify_ssl()
    )
    pw = _read_env_secret(args.pw_env, what="persona password")
    if not pw:
        raise SystemExit("--pw-env must name an env var holding the persona password")
    token = kc.ropc_token(args.realm, args.client, args.user, pw)
    if args.raw:
        print(token)
        return None
    return {"token": token}


# --- parser ---------------------------------------------------------------


def _add_kc_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--kc-url",
        required=True,
        help="Keycloak base URL. NOTE: this reaches the Keycloak ADMIN API "
        "(/admin/*), which by design is NOT exposed on the public ingress (only "
        "OIDC endpoints are). Reach Keycloak directly — e.g. `kubectl port-forward "
        "svc/keycloak 8080:80 -n kamiwaza` then --kc-url http://localhost:8080. For "
        "production, provision the shared realm via the auth chart's init-Job "
        "pipeline instead of this dev command.",
    )
    p.add_argument("--kc-admin-user", default="admin", help="master-realm admin user")
    p.add_argument(
        "--kc-admin-pw-env",
        required=True,
        help="env var holding the master-realm admin password (never argv)",
    )
    p.set_defaults(needs_kc=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kamiwaza-federation",
        description="Shared_idp federation seeding + ReBAC access management.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Platform API root; defaults to KAMIWAZA_BASE_URL. (Unused by 'idp'.)",
    )
    groups = parser.add_subparsers(dest="group")

    # access
    g = groups.add_parser("access", help="ReBAC grants on resources").add_subparsers(
        dest="command"
    )
    for verb, fn in (("grant", cmd_access_grant), ("revoke", cmd_access_revoke)):
        p = g.add_parser(verb, help=f"{verb} a relation on a resource")
        p.add_argument("--subject", required=True, help="e.g. a username / subject id")
        p.add_argument("--relation", required=True, help="e.g. viewer / editor / owner")
        p.add_argument("--object", required=True, help="'<namespace>:<id>', e.g. dataset:<urn>")
        p.set_defaults(func=fn, needs_kc=False)
    p = g.add_parser("list", help="list a subject's grants")
    p.add_argument("--subject", required=True)
    p.set_defaults(func=cmd_access_list, needs_kc=False)

    # fed
    g = groups.add_parser("fed", help="shared_idp federation lifecycle").add_subparsers(
        dest="command"
    )
    p = g.add_parser("pair", help="pair two clusters in shared_idp mode")
    p.add_argument("--name", required=True, help="local name for the federation")
    p.add_argument("--role", required=True, choices=["initiator", "receiver"])
    p.add_argument(
        "--remote-url",
        default=None,
        help="peer API root, e.g. https://peer/api (required for --role initiator; "
        "omit for --role receiver — the receiver row is created without it)",
    )
    p.add_argument("--shared-issuer", required=True, help="shared realm issuer URL")
    p.add_argument("--shared-ca-file", default=None, help="PEM CA for the shared issuer's TLS")
    p.add_argument(
        "--preshared-key-env",
        default=None,
        help="env var holding the shared PSK (never argv); MUST match on both "
        "clusters. Omitted -> a fresh UUID4 is minted.",
    )
    p.add_argument(
        "--remote-admin-token-env",
        default=None,
        help="env var holding the peer admin token (never argv)",
    )
    p.set_defaults(func=cmd_fed_pair, needs_kc=False)
    p = g.add_parser("status", help="list federation posture")
    p.add_argument("--name", default=None, help="filter by name / id")
    p.set_defaults(func=cmd_fed_status, needs_kc=False)
    p = g.add_parser("allow-user", help="allowlist a brokered user + seed ReBAC")
    p.add_argument("--federation", required=True, help="receiver federation id")
    p.add_argument("--external-id", required=True, help="'<sub>@<source-cluster-id>'")
    p.add_argument(
        "--seed",
        action="append",
        help="repeatable '<namespace>:<id>:<relation>', e.g. dataset:<urn>:viewer",
    )
    p.set_defaults(func=cmd_fed_allow_user, needs_kc=False)
    p = g.add_parser("unpair", help="disconnect (tear down) a federation")
    p.add_argument("--name", required=True, help="federation name")
    p.add_argument(
        "--force",
        action="store_true",
        help="tear down without waiting for the peer's ack (peer already gone)",
    )
    p.set_defaults(func=cmd_fed_unpair, needs_kc=False)

    # dataset
    g = groups.add_parser("dataset", help="gated dataset setup").add_subparsers(
        dest="command"
    )
    p = g.add_parser("gated", help="create a file dataset bound to a gate")
    p.add_argument("--name", required=True)
    p.add_argument("--path", required=True, help="on-cluster file path")
    p.add_argument("--gate", required=True, help="gate classpath, e.g. acme_gates...MiniClearanceGate")
    p.add_argument("--gate-config", default=None, help="JSON gate config (default {})")
    p.add_argument("--description", default=None)
    p.set_defaults(func=cmd_dataset_gated, needs_kc=False)

    # gate
    g = groups.add_parser("gate", help="gate packages").add_subparsers(dest="command")
    p = g.add_parser("install", help="install a hash-pinned gate package")
    p.add_argument("--spec", required=True, help="pip spec, e.g. acme-gates==1.1.0")
    p.add_argument("--hash", required=True, help="'sha256:...' of the wheel")
    p.add_argument("--index-url", default=None, help="pip index override")
    p.set_defaults(func=cmd_gate_install, needs_kc=False)

    # attr
    g = groups.add_parser("attr", help="attribute vocabulary").add_subparsers(
        dest="command"
    )
    p = g.add_parser("declare", help="declare an attribute in the vocabulary")
    p.add_argument("--name", required=True, help="e.g. clearance")
    p.add_argument("--type", default="string")
    p.set_defaults(func=cmd_attr_declare, needs_kc=False)

    # idp (Keycloak-admin)
    g = groups.add_parser(
        "idp",
        help="(DEV/TEST ONLY) Keycloak-side shared-realm seeding — `bootstrap`/"
        "`persona` need DIRECT Keycloak admin access (port-forward). For "
        "production, provision the shared realm declaratively via the auth "
        "chart (see ENG-8571 follow-up).",
    ).add_subparsers(dest="command")
    p = g.add_parser("bootstrap", help="ensure realm + ROPC client + attribute mapper")
    p.add_argument("--realm", required=True, help="shared realm, e.g. federated")
    p.add_argument("--ropc-client", required=True, help="public ROPC client id, e.g. fed-mesh-cli")
    p.add_argument("--attr", action="append", help="attribute mapper(s); default clearance")
    _add_kc_args(p)
    p.set_defaults(func=cmd_idp_bootstrap)
    p = g.add_parser("persona", help="ensure a persona user with attributes")
    p.add_argument("--realm", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--attr", action="append", help="'name=value', e.g. clearance=U")
    p.add_argument("--pw-env", required=True, help="env var holding the persona password")
    _add_kc_args(p)
    p.set_defaults(func=cmd_idp_persona)
    p = g.add_parser("token", help="mint a persona ROPC token (test helper)")
    p.add_argument("--realm", required=True)
    p.add_argument("--client", required=True, help="ROPC client id")
    p.add_argument("--user", required=True)
    p.add_argument("--pw-env", required=True)
    p.add_argument("--raw", action="store_true", help="print only the bare token")
    p.add_argument(
        "--kc-url",
        required=True,
        help="Keycloak base URL. Unlike bootstrap/persona, this only does an "
        "ROPC token grant against the PUBLIC OIDC endpoint (/realms/.../token), "
        "so the normal ingress URL works — no admin access / port-forward needed.",
    )
    p.set_defaults(func=cmd_idp_token, needs_kc=True)

    return parser


def main(
    argv: Optional[list[str]] = None,
    *,
    client_factory: Any = build_client_from_env,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.error("a group + command is required (e.g. 'access grant')")
    # idp commands talk to Keycloak, not the platform API — skip the SDK client.
    if getattr(args, "needs_kc", False):
        client = None
    else:
        api_key = os.environ.get("KAMIWAZA_API_KEY") or os.environ.get(
            "KAMIWAZA_API_TOKEN"
        )
        client = client_factory(base_url=args.base_url, api_key=api_key)
        # Admin-scoped seeding ops (grants, attr, dataset gate) need a credential
        # that carries the caller's ROLES. A PAT/api-key authenticates but is
        # role-limited; a username/password login yields a Bearer token that
        # carries the admin role. Prefer it when KAMIWAZA_USERNAME +
        # KAMIWAZA_PASSWORD are in the env (both env, never argv).
        username = os.environ.get("KAMIWAZA_USERNAME")
        password = os.environ.get("KAMIWAZA_PASSWORD")
        if username and password and hasattr(client, "_auth_service"):
            from kamiwaza_sdk.authentication import UserPasswordAuthenticator

            client.authenticator = UserPasswordAuthenticator(
                username, password, client._auth_service
            )
    result = args.func(args, client=client)
    if result is not None:
        print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
