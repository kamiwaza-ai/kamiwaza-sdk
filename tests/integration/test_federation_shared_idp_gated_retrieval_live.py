"""Required two-cluster shared-IDP mesh job and gated-retrieval edge.

The lane carries both topology markers, provisions every prerequisite before
selection, and treats any skip or denial as failure. It drains mesh retrieval
SSE for exact U/S/TS rows and runs a recoverable job to ``SUCCEEDED`` with a
unique receiver marker. Every persona receives viewer authority only for the
unique dataset; the U submitter additionally receives the explicit cluster-job
executor relation. The receiver execution gate still governs dispatch, and the
job service auto-grants per-job authority only after successful submission.
"""

from __future__ import annotations

import os
import shlex
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator
from urllib.parse import quote

import pytest

from kamiwaza_sdk import (
    KamiwazaClient,
    SharedIdpAuthConfig,
    SharedIdpAuthenticator,
)
from kamiwaza_sdk.token_store import InMemoryTokenStore
from tests.integration import mesh_outcome

from . import _mini_clearance as mc
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


def _required_mesh_call(call: Callable[[], Any]) -> Any:
    """Run one required edge call without translating a denial into a skip."""
    return call()


def _assert_receiver_auth_rejection(call: Callable[[], Any]) -> None:
    """Accept only the receiver's structured peer-JWT validation failure."""
    from kamiwaza_sdk.exceptions import KamiwazaError

    try:
        call()
    except KamiwazaError as exc:
        reason = mesh_outcome.reason_of(exc)
        status = getattr(exc, "status_code", None)
        assert status == 403, (
            "expected peer_jwt_validation_failed from receiver status 403, "
            f"got status {status!r}: {exc!r}"
        )
        assert reason == "peer_jwt_validation_failed", (
            "expected peer_jwt_validation_failed from the receiver's shared-IDP "
            f"validator, got {reason!r}: {exc!r}"
        )
        return
    pytest.fail("non-shared token unexpectedly crossed receiver authentication")


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
    receiver.cluster.set_execution_gate(type=_ALLOW_ALL_EXECUTION_GATE, config={})
    try:
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


def _persona_auth(config: pytest.Config) -> dict:
    """Shared-realm ROPC config for clearance-bearing personas."""
    client_id = os.getenv("SHARED_REALM_CLIENT_ID", "").strip()
    password = os.getenv("FED_PERSONA_PASSWORD", "").strip()
    _require_prerequisite(
        config,
        bool(client_id and password),
        "SHARED_REALM_CLIENT_ID / FED_PERSONA_PASSWORD not set — the personas "
        "need a shared-realm ROPC token with the `clearance` claim",
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
        "verify": verify,
    }


def _fed_name() -> str:
    return f"eng8325-sharedidp-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def _receiver_prereqs(pytestconfig: pytest.Config) -> _EdgePrerequisites:
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
    return _EdgePrerequisites(
        wheel_dir=wi[0],
        index_url=wi[1],
        dataset_path=dataset_path,
        shared=_shared_realm(pytestconfig),
        persona_auth=_persona_auth(pytestconfig),
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
        verify=auth["verify"],
    )
    token_store = InMemoryTokenStore()
    authenticator = SharedIdpAuthenticator(config, token_store=token_store)
    client = KamiwazaClient(
        base_url=base_url,
        authenticator=authenticator,
        verify=auth["verify"],
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


def _provision_personas(
    cleanup: ExitStack,
    initiator_base_url: str,
    receiver: Any,
    identifiers: tuple[str, str, str],
    prerequisites: _EdgePrerequisites,
) -> dict[str, dict[str, Any]]:
    federation_id, source_cluster_id, dataset_urn = identifiers
    auth = prerequisites.persona_auth
    issuer = prerequisites.shared["shared_issuer_url"]
    personas: dict[str, dict[str, Any]] = {}
    for clearance, base in _PERSONAS.items():
        persona = _programmatic_persona_session(
            initiator_base_url,
            {**auth, "issuer": issuer},
            base,
        )
        token = persona["token"]
        sub = mc.jwt_sub(token) or base
        external_id = f"{sub}@{source_cluster_id}"
        receiver._request(
            "POST",
            f"/cluster/federations/{federation_id}/users",
            json={
                "external_id": external_id,
                "initial_tuples": _required_initial_tuples(
                    dataset_urn,
                    job_executor=clearance == "U",
                ),
            },
        )
        cleanup.callback(
            _cleanup_brokered_persona,
            receiver,
            federation_id,
            external_id,
        )
        personas[clearance] = {
            **persona,
            "external_id": external_id,
            "sub": sub,
        }
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
        cleanup,
        clients.initiator.base_url,
        clients.receiver,
        (identities.receiver_federation_id, identities.initiator_cluster_id, urn),
        prerequisites,
    )
    cleanup.enter_context(_temporary_execution_gate(clients.receiver))
    return _EdgeWiring(
        name=pair_request.name,
        urn=urn,
        personas=personas,
        shared=prerequisites.shared,
        verify=bool(prerequisites.persona_auth["verify"]),
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


def test_native_realm_token_rejected_at_receiver_shared_idp_boundary(
    shared_idp_gated_pair,
    live_kamiwaza_session_client,
) -> None:
    """A valid initiator-native token must fail receiver shared-IDP auth."""
    selector = quote(shared_idp_gated_pair["name"], safe="")
    _assert_receiver_auth_rejection(
        lambda: live_kamiwaza_session_client._request(
            "GET",
            f"/mesh/{selector}/api/cluster/diagnose",
        )
    )
