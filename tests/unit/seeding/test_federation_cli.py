"""Unit tests for the kamiwaza-fed CLI (mocked SDK client + Keycloak admin)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from kamiwaza_sdk.seeding.federation import cli


def _run(argv, client=None):
    """Invoke main with an injected mock client; capture the printed JSON."""
    printed = {}

    def _factory(**_kw):
        return client

    # main prints JSON for dict results; grab it via monkeypatching print.
    import builtins

    orig = builtins.print
    builtins.print = lambda *a, **k: printed.setdefault("out", a[0] if a else None)
    try:
        rc = cli.main(argv, client_factory=_factory)
    finally:
        builtins.print = orig
    out = printed.get("out")
    return rc, (json.loads(out) if isinstance(out, str) else out)


# --- access ---------------------------------------------------------------


def test_access_grant_splits_object_on_first_colon():
    mc = MagicMock()
    urn = "urn:li:dataset:(urn:li:dataPlatform:file,mini,PROD)"
    rc, out = _run(
        ["access", "grant", "--subject", "alice", "--relation", "viewer",
         "--object", f"dataset:{urn}"],
        client=mc,
    )
    assert rc == 0
    mc.subjects.grants.assert_called_once_with("alice")
    mc.subjects.grants.return_value.create.assert_called_once_with(
        object_namespace="dataset", object_id=urn, relation="viewer"
    )
    assert out["granted"]["object"] == f"dataset:{urn}"


def test_access_revoke_calls_delete():
    mc = MagicMock()
    rc, _ = _run(
        ["access", "revoke", "--subject", "bob", "--relation", "editor",
         "--object", "dataset:d1"],
        client=mc,
    )
    assert rc == 0
    mc.subjects.grants.return_value.delete.assert_called_once_with(
        object_namespace="dataset", object_id="d1", relation="editor"
    )


def test_access_list_returns_grants():
    mc = MagicMock()
    g = MagicMock()
    g.model_dump.return_value = {"object_id": "d1", "relation": "viewer"}
    mc.subjects.grants.return_value.list.return_value = [g]
    rc, out = _run(["access", "list", "--subject", "carol"], client=mc)
    assert rc == 0
    assert out["subject"] == "carol"
    assert out["grants"] == [{"object_id": "d1", "relation": "viewer"}]


def test_access_bad_object_errors():
    with pytest.raises(SystemExit):
        _run(["access", "grant", "--subject", "a", "--relation", "viewer",
              "--object", "no-colon"], client=MagicMock())


# --- fed ------------------------------------------------------------------


def test_fed_pair_derives_jwks_from_issuer():
    mc = MagicMock()
    mc.federations.pair.return_value.model_dump.return_value = {"id": "fed-1"}
    issuer = "https://kc.example/realms/federated"
    rc, out = _run(
        ["fed", "pair", "--name", "f1", "--role", "initiator",
         "--remote-url", "https://peer/api", "--shared-issuer", issuer + "/"],
        client=mc,
    )
    assert rc == 0
    kwargs = mc.federations.pair.call_args.kwargs
    # issuer normalized (trailing slash stripped), JWKS derived from it (not arbitrary)
    assert kwargs["shared_issuer_url"] == issuer
    assert kwargs["shared_jwks_url"] == issuer + "/protocol/openid-connect/certs"
    assert out["shared_jwks_url"] == issuer + "/protocol/openid-connect/certs"


def test_fed_allow_user_builds_initial_tuples():
    mc = MagicMock()
    urn = "dataset:urn:li:dataset:(urn:li:dataPlatform:file,mini,PROD)"
    rc, out = _run(
        ["fed", "allow-user", "--federation", "fed-1",
         "--external-id", "sub@cluster", "--seed", f"{urn}:viewer"],
        client=mc,
    )
    assert rc == 0
    method, path = mc._request.call_args.args
    body = mc._request.call_args.kwargs["json"]
    assert method == "POST" and path == "/cluster/federations/fed-1/users"
    assert body["external_id"] == "sub@cluster"
    assert body["initial_tuples"] == [
        {"subject": "user:{{user_id}}", "relation": "viewer", "object": urn}
    ]


def test_fed_status_lists():
    mc = MagicMock()
    f = MagicMock()
    f.model_dump.return_value = {
        "id": "fed-1", "remote_cluster_name": "peer", "identity_mode": "shared_idp"
    }
    mc.federations.list.return_value = [f]
    rc, out = _run(["fed", "status"], client=mc)
    assert rc == 0
    mc.federations.list.assert_called_once_with()
    assert out["federations"][0]["identity_mode"] == "shared_idp"


# --- dataset / gate / attr ------------------------------------------------


def test_dataset_gated_creates_file_dataset_with_gate():
    mc = MagicMock()
    mc.datasets.create.return_value = "urn:li:dataset:(x)"
    rc, out = _run(
        ["dataset", "gated", "--name", "d", "--path", "/data/x.csv",
         "--gate", "acme_gates.mini.MiniClearanceGate"],
        client=mc,
    )
    assert rc == 0
    kwargs = mc.datasets.create.call_args.kwargs
    assert kwargs["platform"] == "file"
    assert kwargs["properties"]["path"] == "/data/x.csv"
    gate = json.loads(kwargs["properties"]["gate"])
    assert gate == {"type": "acme_gates.mini.MiniClearanceGate", "config": {}}
    assert out["dataset_urn"] == "urn:li:dataset:(x)"


def test_gate_install_wraps_packages():
    mc = MagicMock()
    mc.gates.packages.install.return_value.model_dump.return_value = {"name": "acme-gates"}
    rc, out = _run(
        ["gate", "install", "--spec", "acme-gates==1.1.0", "--hash", "sha256:abc"],
        client=mc,
    )
    assert rc == 0
    mc.gates.packages.install.assert_called_once_with(
        package_spec="acme-gates==1.1.0", hash_digest="sha256:abc", index_url=None
    )


def test_attr_declare():
    mc = MagicMock()
    rc, out = _run(["attr", "declare", "--name", "clearance"], client=mc)
    assert rc == 0
    mc.cluster.declare_attribute.assert_called_once_with("clearance", type="string")


# --- idp (Keycloak-admin, monkeypatched) ----------------------------------


def test_idp_bootstrap_ensures_realm_client_mapper(monkeypatch):
    kc = MagicMock()
    kc.ensure_realm.return_value = {"realm": "federated", "created": True}
    kc.ensure_ropc_client.return_value = {"client_id": "fed-mesh-cli", "id": "uuid"}
    kc.issuer_url.return_value = "https://kc/realms/federated"
    monkeypatch.setattr(cli, "build_kc_admin", lambda args: kc)
    rc, out = _run(
        ["idp", "bootstrap", "--realm", "federated", "--ropc-client", "fed-mesh-cli",
         "--kc-url", "https://kc", "--kc-admin-pw-env", "KCPW"],
    )
    assert rc == 0
    kc.set_unmanaged_attributes.assert_called_once_with("federated")
    kc.ensure_attribute_mapper.assert_called_once_with("federated", "uuid", attribute="clearance")
    assert out["shared_issuer_url"] == "https://kc/realms/federated"


def test_idp_persona_parses_attrs(monkeypatch):
    kc = MagicMock()
    kc.ensure_user.return_value = {"username": "fed-clr-u", "id": "u1", "created": True}
    monkeypatch.setattr(cli, "build_kc_admin", lambda args: kc)
    monkeypatch.setenv("PPW", "secret")
    rc, out = _run(
        ["idp", "persona", "--realm", "federated", "--user", "fed-clr-u",
         "--attr", "clearance=U", "--pw-env", "PPW",
         "--kc-url", "https://kc", "--kc-admin-pw-env", "KCPW"],
    )
    assert rc == 0
    assert kc.ensure_user.call_args.kwargs["attributes"] == {"clearance": "U"}
    assert out["attributes"] == {"clearance": "U"}


def test_idp_token_raw(monkeypatch, capsys):
    kc = MagicMock()
    kc.ropc_token.return_value = "TOK123"
    monkeypatch.setattr(cli, "build_kc_admin", lambda args: kc)
    monkeypatch.setenv("PPW", "secret")
    monkeypatch.setattr(cli, "KeycloakAdmin", lambda *a, **k: kc)
    rc = cli.main(
        ["idp", "token", "--realm", "federated", "--client", "fed-mesh-cli",
         "--user", "fed-clr-u", "--pw-env", "PPW", "--raw", "--kc-url", "https://kc"],
    )
    assert rc == 0
    assert capsys.readouterr().out.strip() == "TOK123"
