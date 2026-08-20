"""Required two-cluster shared-IDP mesh job and gated-retrieval edge.

The lane carries both topology markers, provisions every prerequisite before
selection, and treats any skip or denial as failure. It drains mesh retrieval
SSE for exact U/S/TS rows and runs a recoverable job to ``SUCCEEDED`` with a
unique receiver marker. Every clearance persona receives viewer authority only
for the unique dataset; the U submitter additionally receives the explicit
cluster-job executor relation. Tenant-negative personas are receiver-allowlisted
with no initial tuples so the producer tenant boundary is the exact denial under
test. The receiver execution gate still governs dispatch, and the job service
auto-grants per-job authority only after successful submission.
"""

from __future__ import annotations

import os
import shlex
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote

import pytest

from kamiwaza_extensions_lib._jwt import decode_jwt_payload
from kamiwaza_sdk import (
    KamiwazaClient,
    SharedIdpAuthConfig,
    SharedIdpAuthenticator,
)
from kamiwaza_sdk.services.federation_credentials import federation_credential_headers
from kamiwaza_sdk.token_store import InMemoryTokenStore
from tests.integration import mesh_outcome

from . import _mini_clearance as mc
from ._shared_idp_fixture import DEFAULT_TENANT_ID, TENANT_NEGATIVE_PERSONAS
from .required_federation_edge_setup import pair_required_edge, provision_gated_dataset

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.requires_two_clusters,
    pytest.mark.requires_shared_idp,
    pytest.mark.requires_owned_shared_realm,
]

_PERSONAS = {"U": "fed-clr-u", "S": "fed-clr-s", "TS": "fed-clr-ts"}
_UNONBOARDED_PERSONA = "fed-clr-unonboarded"

_ALLOW_ALL_EXECUTION_GATE = (
    "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate"
)
_SHARED_IDP_POLICY = mesh_outcome.MeshPolicy(
    identity_arranged=True,
    admission_is_the_assertion=False,
    context="shared_idp gated retrieval compatibility policy",
)


@dataclass(frozen=True)
class _EdgePrerequisites:
    wheel_dir: str
    index_url: str
    dataset_path: str
    shared: dict[str, str]
    persona_auth: dict[str, Any]


@dataclass(frozen=True)
class _PairRequest:
    name: str
    peer_url: str
    psk: str
    shared: dict[str, str]


@dataclass(frozen=True)
class _EdgeWiring:
    name: str
    urn: str
    personas: dict[str, dict[str, Any]]
    shared: dict[str, str]
    verify: bool
    source_cluster_id: str
    receiver_cluster_id: str


@dataclass(frozen=True)
class _EdgeClients:
    initiator: Any
    receiver: Any


@dataclass(frozen=True)
class _PersonaProvisioning:
    cleanup: ExitStack
    initiator_base_url: str
    receiver: Any
    federation_id: str
    source_cluster_id: str
    dataset_urn: str
    auth: dict[str, Any]


@dataclass(frozen=True)
class _TenantRejectionCase:
    persona_key: str
    expected_status: int
    expected_reason: str


_TENANT_REJECTION_CASES = (
    _TenantRejectionCase("missing-canonical", 401, "tenant_required"),
    _TenantRejectionCase("legacy-only", 401, "tenant_required"),
    _TenantRejectionCase(
        "canonical-nondefault",
        403,
        "mesh_tenant_not_admitted",
    ),
)


def _required_mesh_call(call: Callable[[], Any]) -> Any:
    """Run one required edge call without translating a denial into a skip."""
    return call()


def _assert_receiver_onboarding_rejection(call: Callable[[], Any]) -> None:
    """Accept only the receiver's structured allowlist rejection."""
    from kamiwaza_sdk.exceptions import KamiwazaError

    try:
        call()
    except KamiwazaError as exc:
        reason = mesh_outcome.reason_of(exc)
        status = getattr(exc, "status_code", None)
        assert status == 403, (
            "expected unauthorized_brokered_user from receiver status 403, "
            f"got status {status!r}: {exc!r}"
        )
        assert reason == "unauthorized_brokered_user", (
            "expected unauthorized_brokered_user from the receiver allowlist, "
            f"got {reason!r}: {exc!r}"
        )
        return
    pytest.fail("unonboarded shared-IDP user unexpectedly crossed the allowlist")


def _require_prerequisite(config: pytest.Config, condition: bool, message: str) -> None:
    if condition:
        return
    if config.getoption("require_federation_edge"):
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


def _required_initial_tuples(
    dataset_urn: str,
    *,
    job_executor: bool,
) -> list[dict[str, str]]:
    """Fixture-scoped retrieval authority plus one explicit job submitter."""
    tuples = [
        {
            "subject": "user:{{user_id}}",
            "relation": "viewer",
            "object": f"dataset:{dataset_urn}",
        }
    ]
    if job_executor:
        tuples.append(
            {
                "subject": "user:{{user_id}}",
                "relation": "executor",
                "object": "cluster_jobs:__all__",
            }
        )
    return tuples


def _assert_terminal_mesh_job(result: Any, expected_marker: str) -> None:
    """Require successful receiver execution and the exact result marker."""
    assert result.status == "SUCCEEDED", (
        f"required mesh job did not succeed: status={result.status} result={result}"
    )
    marker = getattr(result, "probe", None)
    if marker is None and isinstance(result.result, dict):
        marker = result.result.get("probe")
    assert marker == expected_marker, (
        "required receiver marker did not round-trip: "
        f"expected={expected_marker!r} got={marker!r} result={result}"
    )


def _assert_receiver_job_provenance(
    status: Any,
    job_id: str,
    source_cluster_id: str,
    receiver_cluster_id: str,
) -> None:
    """Prove the authoritative job record was created from mesh ingress."""
    assert isinstance(status, dict), f"unexpected job status payload: {status!r}"
    assert str(status.get("id")) == str(job_id), status
    assert status.get("source") == "mesh", status
    assert str(status.get("source_cluster_id")) == str(source_cluster_id), status
    assert source_cluster_id != receiver_cluster_id


def _current_execution_gate(receiver: Any) -> Any:
    """Read the pre-existing binding without hiding non-404 failures."""
    from kamiwaza_sdk.exceptions import APIError

    try:
        return receiver.cluster.get_execution_gate()
    except APIError as exc:
        if exc.status_code == 404:
            return None
        raise


def _restore_execution_gate(receiver: Any, previous: Any) -> None:
    """Restore the receiver's execution-gate state after the required edge."""
    if previous is None:
        receiver.cluster.clear_execution_gate()
        return
    receiver.cluster.set_execution_gate(type=previous.type, config=previous.config)


@contextmanager
def _temporary_execution_gate(receiver: Any) -> Iterator[None]:
    """Bind the live-test gate and always restore the receiver's prior state."""
    previous = _current_execution_gate(receiver)
    try:
        receiver.cluster.set_execution_gate(type=_ALLOW_ALL_EXECUTION_GATE, config={})
        yield
    finally:
        _restore_execution_gate(receiver, previous)


def _shared_realm(config: pytest.Config) -> dict[str, str]:
    issuer = os.getenv("SHARED_ISSUER_URL", "").strip()
    _require_prerequisite(
        config,
        bool(issuer),
        "SHARED_ISSUER_URL not set — shared_idp pairing needs a shared realm "
        "that projects the `clearance` claim into brokered JWTs",
    )
    cfg = {"shared_issuer_url": issuer}
    for env, key in (
        ("SHARED_JWKS_URL", "shared_jwks_url"),
        ("SHARED_CA_PEM", "shared_ca_pem"),
    ):
        val = os.getenv(env, "").strip()
        if val:
            cfg[key] = val
    return cfg


def _shared_idp_verify(
    shared: dict[str, str],
    *,
    platform_verify: bool,
    temp_root: Path,
) -> bool | str:
    """Materialize shared-realm CA content for direct OIDC token requests."""
    ca_pem = shared.get("shared_ca_pem")
    if not ca_pem:
        return platform_verify
    ca_path = temp_root / "shared-idp-ca.pem"
    ca_path.write_text(ca_pem, encoding="utf-8")
    ca_path.chmod(0o600)
    return str(ca_path)


def _persona_auth(
    config: pytest.Config,
    *,
    shared: dict[str, str],
    temp_root: Path,
) -> dict:
    """Shared-realm ROPC config for default-tenant clearance personas."""
    client_id = os.getenv("SHARED_REALM_CLIENT_ID", "").strip()
    password = os.getenv("FED_PERSONA_PASSWORD", "").strip()
    _require_prerequisite(
        config,
        bool(client_id and password),
        "SHARED_REALM_CLIENT_ID / FED_PERSONA_PASSWORD not set — the personas "
        "need a shared-realm ROPC token with `clearance` and explicit "
        "`tenant_id=__default__` claims",
    )
    verify = os.getenv("KAMIWAZA_VERIFY_SSL", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    return {
        "client_id": client_id,
        "client_secret": os.getenv("SHARED_REALM_CLIENT_SECRET", "").strip() or None,
        "password": password,
        "idp_verify": _shared_idp_verify(
            shared,
            platform_verify=verify,
            temp_root=temp_root,
        ),
        "platform_verify": verify,
        "allow_insecure_tls": verify is False,
    }


def _fed_name() -> str:
    return f"eng8325-sharedidp-{uuid.uuid4().hex[:8]}"


def _assert_default_tenant_claim(token: str) -> None:
    """Preflight claim shape; the receiver still validates token cryptography."""
    claims = decode_jwt_payload(token)
    if claims.get("tenant_id") != DEFAULT_TENANT_ID:
        raise AssertionError("shared-IDP access token must carry tenant_id=__default__")


def _assert_tenant_claim_shape(
    token: str,
    expected: dict[str, str],
    *,
    context: str,
) -> None:
    """Validate only tenant claims without exposing token or claim contents."""
    claims = decode_jwt_payload(token)
    observed = {key: claims[key] for key in ("tenant_id", "tenant") if key in claims}
    if observed != expected:
        raise AssertionError(
            f"shared-IDP access token has unexpected tenant claim shape for {context}"
        )


def _tenant_rejection_reason(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    return detail if isinstance(detail, str) else None


@pytest.fixture(scope="module")
def _receiver_prereqs(
    pytestconfig: pytest.Config,
    tmp_path_factory: pytest.TempPathFactory,
) -> _EdgePrerequisites:
    wi = mc.wheel_and_index()
    _require_prerequisite(
        pytestconfig,
        wi is not None,
        "gate-packages wheel/index not configured on the receiver",
    )
    assert wi is not None
    dataset_path = os.getenv("MINI_CLEARANCE_DATASET_PATH", "").strip()
    _require_prerequisite(
        pytestconfig,
        bool(dataset_path),
        "MINI_CLEARANCE_DATASET_PATH not set (receiver fixture file)",
    )
    shared = _shared_realm(pytestconfig)
    return _EdgePrerequisites(
        wheel_dir=wi[0],
        index_url=wi[1],
        dataset_path=dataset_path,
        shared=shared,
        persona_auth=_persona_auth(
            pytestconfig,
            shared=shared,
            temp_root=tmp_path_factory.mktemp("shared-idp-ca"),
        ),
    )


def _cleanup_brokered_persona(
    receiver: Any, federation_id: str, external_id: str
) -> None:
    """Revoke the exact temporary allowlist row and cancel its active jobs."""
    encoded_external_id = quote(external_id, safe="")
    receiver._request(
        "POST",
        f"/cluster/federations/{federation_id}/users/{encoded_external_id}/revoke",
        params={"cancel_in_flight_jobs": "true"},
    )


def _programmatic_persona_session(
    base_url: str,
    auth: dict[str, Any],
    username: str,
) -> dict[str, Any]:
    """Create a direct shared-realm session and prove one real refresh grant."""
    config = SharedIdpAuthConfig(
        issuer=auth["issuer"],
        client_id=auth["client_id"],
        client_secret=auth["client_secret"],
        username=username,
        password=auth["password"],
        verify=auth["idp_verify"],
        allow_insecure_tls=auth["allow_insecure_tls"],
    )
    token_store = InMemoryTokenStore()
    authenticator = SharedIdpAuthenticator(config, token_store=token_store)
    client = KamiwazaClient(
        base_url=base_url,
        authenticator=authenticator,
        verify=auth["platform_verify"],
        owns_authenticator=True,
    )
    authenticator.authenticate(client.session)
    authenticator.refresh_token(client.session)
    token = authenticator.get_access_token(client.session)
    assert token, "shared-IDP refresh produced no access token"
    assert token_store.load() is not None
    assert token_store.load().refresh_token  # type: ignore[union-attr]
    return {
        "client": client,
        "authenticator": authenticator,
        "token": token,
    }


def _active_persona_session(persona: dict[str, Any]) -> tuple[Any, str]:
    client = persona["client"]
    token = persona["authenticator"].get_access_token(client.session)
    assert token, "shared-IDP persona has no current access token"
    return client, token


def _open_persona(
    provisioning: _PersonaProvisioning,
    username: str,
) -> dict[str, Any]:
    persona = _programmatic_persona_session(
        provisioning.initiator_base_url,
        provisioning.auth,
        username,
    )
    provisioning.cleanup.callback(persona["client"].close)
    return persona


def _allowlist_persona(
    provisioning: _PersonaProvisioning,
    persona: dict[str, Any],
    initial_tuples: list[dict[str, str]],
) -> dict[str, Any]:
    sub = mc.jwt_sub(persona["token"])
    assert sub, "shared-IDP token has no subject claim"
    external_id = f"{sub}@{provisioning.source_cluster_id}"
    provisioning.receiver._request(
        "POST",
        f"/cluster/federations/{provisioning.federation_id}/users",
        json={"external_id": external_id, "initial_tuples": initial_tuples},
    )
    provisioning.cleanup.callback(
        _cleanup_brokered_persona,
        provisioning.receiver,
        provisioning.federation_id,
        external_id,
    )
    return {**persona, "external_id": external_id, "sub": sub}


def _provision_personas(
    provisioning: _PersonaProvisioning,
) -> dict[str, dict[str, Any]]:
    personas: dict[str, dict[str, Any]] = {}
    for clearance, base in _PERSONAS.items():
        persona = _open_persona(provisioning, base)
        _assert_default_tenant_claim(persona["token"])
        tuples = _required_initial_tuples(
            provisioning.dataset_urn,
            job_executor=clearance == "U",
        )
        personas[clearance] = _allowlist_persona(provisioning, persona, tuples)

    unonboarded = _open_persona(provisioning, _UNONBOARDED_PERSONA)
    _assert_default_tenant_claim(unonboarded["token"])
    personas["unonboarded"] = unonboarded

    for case_id, (username, attributes) in TENANT_NEGATIVE_PERSONAS.items():
        persona = _open_persona(provisioning, username)
        expected = {
            key: attributes[key] for key in ("tenant_id", "tenant") if key in attributes
        }
        _assert_tenant_claim_shape(persona["token"], expected, context=case_id)
        personas[case_id] = _allowlist_persona(provisioning, persona, [])
    return personas


def _wire_required_edge(
    cleanup: ExitStack,
    clients: _EdgeClients,
    pair_request: _PairRequest,
    prerequisites: _EdgePrerequisites,
) -> _EdgeWiring:
    identities = pair_required_edge(
        cleanup,
        clients,
        pair_request,
    )
    urn = provision_gated_dataset(
        cleanup,
        clients.receiver,
        prerequisites,
        pair_request.name,
    )
    personas = _provision_personas(
        _PersonaProvisioning(
            cleanup=cleanup,
            initiator_base_url=clients.initiator.base_url,
            receiver=clients.receiver,
            federation_id=identities.receiver_federation_id,
            source_cluster_id=identities.initiator_cluster_id,
            dataset_urn=urn,
            auth={
                **prerequisites.persona_auth,
                "issuer": prerequisites.shared["shared_issuer_url"],
            },
        )
    )
    cleanup.enter_context(_temporary_execution_gate(clients.receiver))
    return _EdgeWiring(
        name=pair_request.name,
        urn=urn,
        personas=personas,
        shared=prerequisites.shared,
        verify=bool(prerequisites.persona_auth["platform_verify"]),
        source_cluster_id=identities.initiator_cluster_id,
        receiver_cluster_id=identities.receiver_cluster_id,
    )


@pytest.fixture(scope="module")
def shared_idp_gated_pair(
    _receiver_prereqs: _EdgePrerequisites,
    live_kamiwaza_session_client: Any,
    live_kamiwaza_peer_client: Any,
    live_peer_base_url: str,
) -> Iterator[dict]:
    """Provision the required shared-IDP edge and tear it down at exit."""
    prerequisites = _receiver_prereqs
    initiator = live_kamiwaza_session_client
    receiver = live_kamiwaza_peer_client
    name = _fed_name()
    pair_request = _PairRequest(
        name=name,
        peer_url=live_peer_base_url,
        psk=uuid.uuid4().hex,
        shared=prerequisites.shared,
    )

    with ExitStack() as cleanup:
        wiring = _wire_required_edge(
            cleanup,
            _EdgeClients(initiator, receiver),
            pair_request,
            prerequisites,
        )
        yield wiring.__dict__


@pytest.mark.parametrize("clearance", ["U", "S", "TS"])
def test_required_mesh_retrieval_returns_exact_post_gate_rows(
    clearance, shared_idp_gated_pair, live_kamiwaza_session_client
) -> None:
    """A shared_idp persona mesh-retrieves exactly its allowed rows — known answer."""
    wiring = shared_idp_gated_pair
    initiator = live_kamiwaza_session_client
    name, urn = wiring["name"], wiring["urn"]
    persona, token = _active_persona_session(wiring["personas"][clearance])

    def _retrieve():
        # Create the retrieval job over the mesh AND drain its gated SSE stream
        # over the mesh — the results + gate_audit footer arrive on the stream,
        # not the async create response.
        return mc.mesh_retrieve_through_gate(
            persona, initiator.base_url, token, name, urn, verify=wiring["verify"]
        )

    rows, gate_audit = _required_mesh_call(_retrieve)
    mc.assert_persona_result(clearance, rows, gate_audit)


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=case.persona_key) for case in _TENANT_REJECTION_CASES],
)
def test_required_mesh_retrieval_rejects_invalid_tenant(
    case: _TenantRejectionCase,
    shared_idp_gated_pair: dict[str, Any],
    live_kamiwaza_session_client: Any,
) -> None:
    """Tenant-negative users fail at the producer mesh authority boundary.

    Persona setup already proves a real refresh grant. This uses that same
    authenticated session directly so the SDK's 401 translation cannot discard
    the response status/body before this exact denial oracle inspects them.
    """
    wiring = shared_idp_gated_pair
    persona, token = _active_persona_session(wiring["personas"][case.persona_key])
    selector = quote(wiring["name"], safe="")
    url = (
        f"{live_kamiwaza_session_client.base_url.rstrip('/')}"
        f"/mesh/{selector}/api/retrieval/jobs"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        **federation_credential_headers(wiring["name"]),
    }

    with persona.session.post(
        url,
        json={"dataset_urn": wiring["urn"]},
        headers=headers,
        verify=wiring["verify"],
        timeout=120,
    ) as response:
        assert response.status_code == case.expected_status
        try:
            payload = response.json()
        except ValueError:
            pytest.fail("tenant rejection returned a non-JSON body", pytrace=False)

    assert _tenant_rejection_reason(payload) == case.expected_reason


def test_required_mesh_dataset_list_returns_only_authorized_fixture(
    shared_idp_gated_pair,
) -> None:
    """List receiver datasets through mesh with receiver-local ReBAC filtering."""
    wiring = shared_idp_gated_pair
    persona, _token = _active_persona_session(wiring["personas"]["U"])

    datasets = _required_mesh_call(
        lambda: persona.catalog.datasets.list(
            target_cluster=wiring["name"],
        )
    )

    assert [str(dataset.urn) for dataset in datasets] == [wiring["urn"]]


def test_required_mesh_job_reaches_receiver_and_returns_marker(
    shared_idp_gated_pair,
) -> None:
    """Run a recoverable job on the receiver and assert its exact payload."""
    wiring = shared_idp_gated_pair
    persona, _token = _active_persona_session(wiring["personas"]["U"])
    marker = f"eng10050-{uuid.uuid4().hex}"
    script = (
        "import json\n"
        f"print('KZ_MESH_RUN_ON_JSON::' + json.dumps({{'probe': {marker!r}}}))\n"
    )

    result = _required_mesh_call(
        lambda: persona.jobs.run(
            entrypoint="python3 -c " + shlex.quote(script),
            target_cluster=wiring["name"],
            timeout_seconds=120,
            recoverable=True,
        )
    )

    _assert_terminal_mesh_job(result, marker)
    selector = quote(wiring["name"], safe="")
    status = persona._request(
        "GET",
        f"/mesh/{selector}/api/cluster/jobs/{result.job_id}/status",
    )
    _assert_receiver_job_provenance(
        status,
        str(result.job_id),
        wiring["source_cluster_id"],
        wiring["receiver_cluster_id"],
    )


def test_unonboarded_shared_idp_user_rejected_by_receiver_allowlist(
    shared_idp_gated_pair,
) -> None:
    """A valid shared-IDP token still requires receiver-side onboarding."""
    selector = quote(shared_idp_gated_pair["name"], safe="")
    persona, _token = _active_persona_session(
        shared_idp_gated_pair["personas"]["unonboarded"]
    )
    _assert_receiver_onboarding_rejection(
        lambda: persona._request(
            "GET",
            f"/mesh/{selector}/api/cluster/diagnose",
        )
    )
