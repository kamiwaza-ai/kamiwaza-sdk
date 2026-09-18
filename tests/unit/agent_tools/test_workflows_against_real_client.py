"""Drive every workflow through a real client with only the transport stubbed.

A service-level fake cannot see two whole classes of defect, because it
replaces the code that carries them. A workflow whose request body holds a
value ``json.dumps`` cannot encode fails in ``requests``' own preparation, and
a workflow that publishes what a service returns by default publishes bytes
when that default is bytes. Both are invisible to a double that answers in
models, and both are visible the moment a real ``KamiwazaClient`` runs with
``requests.Session.send`` replaced.

So the client here is real: the services, the request building, the pydantic
validation of every response and the payload assembly all run. Only the socket
is missing, and ``socket.socket.connect`` is stubbed to fail the test rather
than reach a network, so "no network call" is asserted rather than assumed.

Every canned answer is the shape the service that reads it parses, keyed by the
method and path that service calls. An unmatched request raises rather than
returning a blank body: a request nobody staged an answer for is a request this
file has stopped covering, and it must fail loudly instead of passing on a
default.
"""

from __future__ import annotations

import dataclasses
import json
import re
import socket
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlsplit

import pytest
import requests
from pydantic import BaseModel

from kamiwaza_sdk.agent_tools import workflows
from kamiwaza_sdk.agent_tools.workflows._contract import WORKFLOWS
from kamiwaza_sdk.client import KamiwazaClient

pytestmark = pytest.mark.unit

_BASE_URL = "https://kamiwaza.test/api"

_MODEL_ID = "11111111-1111-4111-8111-111111111111"
_DEPLOYMENT_ID = "22222222-2222-4222-8222-222222222222"
_INSTANCE_ID = "33333333-3333-4333-8333-333333333333"
_CONNECTOR_ID = "44444444-4444-4444-8444-444444444444"
_WORKROOM_ID = "55555555-5555-4555-8555-555555555555"
_TEMPLATE_ID = "66666666-6666-4666-8666-666666666666"
_APP_DEPLOYMENT_ID = "77777777-7777-4777-8777-777777777777"
_FEDERATION_ID = "88888888-8888-4888-8888-888888888888"
_NODE_ID = "99999999-9999-4999-8999-999999999999"
_DATASET_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,sales,PROD)"
_JOB_ID = "ingest-job-1"
_HASH = "sha256:" + "ab" * 32
_TIMESTAMP = "2026-01-01T00:00:00Z"
_MODEL_CONFIG_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

#: The zip bundle the workroom export route answers with. A real bundle is
#: bytes on the wire, which is the fact High #4 turns on: a workflow that
#: publishes the service's default return publishes these bytes.
_BUNDLE = b"PK\x03\x04stub-bundle"

_GATE_PACKAGE_STATE = {
    "name": "acme-gates",
    "package_spec": "acme-gates==1.2.3",
    "version": "1.2.3",
    "hash_digest": _HASH,
    "installed_at": "2026-01-01T00:00:00Z",
    "installed_by": "operator",
    "status": "active",
    "classpaths": ["acme_gates.Gate"],
}
_GATE_INSTALL_RESULT = {
    "package": _GATE_PACKAGE_STATE,
    "install_duration_seconds": 1.5,
    "audit_event_id": "audit-1",
}

#: Canned platform answers, as ``(method, path pattern, body)``. The path is
#: matched with ``re.fullmatch`` against the request path with the base URL's
#: ``/api`` prefix removed, so ordering carries no meaning and two routes that
#: share a prefix cannot shadow each other. A ``bytes`` body is served as
#: ``application/zip``; anything else is JSON.
_ANSWERS: tuple[tuple[str, str, Any], ...] = (
    ("POST", r"/models/search/", {
        "results": [{"id": _MODEL_ID, "model": {"id": _MODEL_ID, "name": "test-model"}}],
        "total_results": 1,
    }),
    ("POST", r"/serving/deploy_model", _DEPLOYMENT_ID),
    ("POST", r"/serving/estimate_model_vram", {
        "computed_vram_estimate": 8.0e9,
        "highest_node_vram": 8.0e10,
    }),
    ("GET", r"/model_configs/", [{
        "id": _MODEL_CONFIG_ID,
        "m_id": _MODEL_ID,
        "default": True,
        "created_at": _TIMESTAMP,
    }]),
    ("GET", r"/serving/deployment/[^/]+/status", "DEPLOYED"),
    ("GET", r"/serving/deployment/[^/]+", {
        "id": _DEPLOYMENT_ID,
        "m_id": _MODEL_ID,
        "m_config_id": _MODEL_CONFIG_ID,
        "requested_at": _TIMESTAMP,
        "status": "DEPLOYED",
        "instances": [],
    }),
    ("DELETE", r"/serving/deployment/[^/]+", True),
    ("GET", r"/serving/model_instances", [{
        "id": _INSTANCE_ID,
        "deployment_id": _DEPLOYMENT_ID,
        "deployed_at": _TIMESTAMP,
        "host_name": "node-a",
        "listen_port": 8080,
        "status": "DEPLOYED",
    }]),
    ("GET", r"/cluster/get_running_nodes", [{
        "id": _NODE_ID,
        "active": True,
        "status": "RUNNING",
    }]),
    ("POST", r"/catalog/datasets/", _DATASET_URN),
    ("GET", r"/catalog/datasets/by-urn", {
        "urn": _DATASET_URN,
        "name": "sales",
        "platform": "snowflake",
        "environment": "PROD",
    }),
    ("POST", r"/ingestion/ingest/run", {
        "urns": [_DATASET_URN],
        "status": "completed",
        "errors": [],
    }),
    ("GET", r"/ingestion/ingest/status/[^/]+", {
        "job_id": _JOB_ID,
        "status": "completed",
        "error_count": 0,
        "created_urns": [_DATASET_URN],
    }),
    ("POST", r"/retrieval/jobs", {
        "job_id": "retrieval-job-1",
        "dataset": {"urn": _DATASET_URN, "platform": "snowflake"},
        "transport": "inline",
        "status": "succeeded",
        "inline": {
            "data": [{"region": "emea", "revenue": 12}],
            "row_count": 1,
            "media_type": "application/json",
        },
    }),
    ("POST", r"/enclaves/connectors/", {
        "id": _CONNECTOR_ID,
        "name": "sales-share",
        "source_type": "s3",
        "connector_type": "s3",
        "connection_config": {"bucket": "sales"},
        "tags": [],
        "allowed_roles": [],
        "require_encryption": True,
        "enabled": True,
        "system_high": "false",
        "error_count": 0,
        "created_at": _TIMESTAMP,
        "created_by": "operator",
    }),
    ("POST", r"/enclaves/connectors/[^/]+/trigger_ingest", {
        "status": "accepted",
        "connector_id": _CONNECTOR_ID,
    }),
    ("POST", r"/workrooms/", {
        "id": _WORKROOM_ID,
        "tenant_id": "tenant-1",
        "owner_user_id": "operator",
        "name": "release-room",
        "type": "persistent",
        "status": "active",
        "created_at": _TIMESTAMP,
    }),
    ("POST", r"/workrooms/[^/]+/enter", {
        "workroom_id": _WORKROOM_ID,
        "access_token": "workroom-token",
    }),
    ("POST", r"/workrooms/[^/]+/export", _BUNDLE),
    ("GET", r"/workrooms/[^/]+/export/manifest", {
        "workroom_id": _WORKROOM_ID,
        "items": [{"type": "dataset", "name": "sales", "exportable": True}],
    }),
    ("GET", r"/apps/app_templates", [{
        "id": _TEMPLATE_ID,
        "name": "kaizen",
        "version": "1.0.0",
        "source_type": "kamiwaza",
        "visibility": "private",
        "compose_yml": "services: {}",
        "risk_tier": 0,
        "created_at": _TIMESTAMP,
    }]),
    ("POST", r"/apps/deploy_app", {
        "id": _APP_DEPLOYMENT_ID,
        "name": "kaizen",
        "template_id": _TEMPLATE_ID,
        "status": "RUNNING",
        "requested_at": _TIMESTAMP,
        "created_at": _TIMESTAMP,
    }),
    ("GET", r"/apps/deployment/[^/]+/status", "RUNNING"),
    ("POST", r"/authz/gate-packages", _GATE_INSTALL_RESULT),
    ("PUT", r"/authz/gate-packages/[^/]+", _GATE_INSTALL_RESULT),
    ("GET", r"/authz/gate-packages/[^/]+", _GATE_PACKAGE_STATE),
    ("GET", r"/authz/gate-packages", {
        "items": [_GATE_PACKAGE_STATE],
        "total": 1,
    }),
    ("POST", r"/cluster/federations", {
        "id": _FEDERATION_ID,
        "remote_cluster_name": "peer",
        "role": "initiator",
        "status": "PAIRING",
    }),
    ("POST", r"/cluster/federations/[^/]+/pair", {
        "id": _FEDERATION_ID,
        "remote_cluster_name": "peer",
        "role": "initiator",
        "status": "PAIRED",
    }),
    ("PUT", r"/authz/subjects/[^/]+", {
        "id": "kc-subject-1",
        "username": "analyst",
        "attributes": {},
    }),
    ("GET", r"/authz/subjects/[^/]+/grants", [{
        "object_namespace": "dataset",
        "object_id": _DATASET_URN,
        "relation": "reader",
    }]),
    ("POST", r"/auth/tuples", {"written": True}),
    ("POST", r"/auth/check", {
        "allow": True,
        "decision_id": "decision-1",
        "reason": "the subject holds the relation",
    }),
)

#: Plausible arguments for every workflow, keyed by the name its own
#: :class:`WorkflowSpec` registers and read off its own parameters. The
#: optional ones are left out on purpose: an agent filling the published schema
#: sends the required fields, so the defaults are what a real call exercises —
#: which is how a service default of bytes reaches a published payload.
_ARGUMENTS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "find_and_deploy_model": (("llama",), {}),
    "deploy_and_connect_model": ((_MODEL_ID,), {}),
    "preflight_and_deploy_model": ((_MODEL_ID, _MODEL_ID), {}),
    "diagnose_deployment": ((_DEPLOYMENT_ID,), {}),
    "retire_deployment": ((_DEPLOYMENT_ID,), {}),
    "ingest_dataset_and_index": (
        (workflows.DatasetTarget(name="sales", platform="snowflake"), "s3"),
        {},
    ),
    "run_source_ingestion": (("s3",), {}),
    "complete_dataset_ingestion": (
        (_JOB_ID, workflows.DatasetTarget(name="sales", platform="snowflake")),
        {},
    ),
    "rag_query": ((_DATASET_URN,), {}),
    "enclave_ingest": (("sales-share", "s3", "s3", {"bucket": "sales"}), {}),
    "create_workroom_and_enter": (
        (workflows.WorkroomDraft(name="release-room", workroom_type="persistent"),),
        {},
    ),
    "export_workroom_bundle": ((_WORKROOM_ID,), {}),
    "deploy_app_from_garden": ((workflows.AppRequest(name="kaizen"),), {}),
    "install_and_bind_gate_package": (
        (
            workflows.GatePackageRef(
                name="acme-gates",
                spec="acme-gates==1.2.3",
                hash_digest=_HASH,
            ),
        ),
        {},
    ),
    "replace_gate_package": (
        (
            workflows.GatePackageRef(
                name="acme-gates",
                spec="acme-gates==1.2.4",
                hash_digest=_HASH,
            ),
        ),
        {},
    ),
    "pair_federation_and_allow_user": (
        (
            workflows.FederationEnrolment(
                name="peer",
                role="initiator",
                username="analyst",
                remote_url="https://peer.kamiwaza.test",
            ),
        ),
        {},
    ),
    "grant_subject_access": (("analyst", "reader", "dataset", _DATASET_URN), {}),
}


def _registered_functions() -> dict[str, Callable[..., Any]]:
    """Return every registered workflow function, keyed by its published name.

    Read from the functions rather than from a list of imports, because
    :func:`register` is what puts a spec on a function: a workflow renamed at
    its registration is found here under the new name, and one that is never
    registered is not found at all.

    Returns:
        The callables, keyed by the name their spec registers.
    """
    found: dict[str, Callable[..., Any]] = {}
    for value in vars(workflows).values():
        spec = getattr(value, "workflow", None)
        if spec is not None and spec.name in WORKFLOWS:
            found[spec.name] = value
    return found


_FUNCTIONS = _registered_functions()


class _Transport:
    """The stubbed transport, and the record of what reached it."""

    def __init__(self) -> None:
        """Start with no requests recorded."""
        self.calls: list[tuple[str, str]] = []

    def answer(self, request: requests.PreparedRequest) -> requests.Response:
        """Return the canned answer for one prepared request.

        Args:
            request: The request ``requests`` prepared, body and all.

        Returns:
            A response carrying the staged body.

        Raises:
            AssertionError: No answer is staged for this method and path.
        """
        method = (request.method or "").upper()
        path = urlsplit(request.url or "").path
        if path.startswith("/api"):
            path = path[len("/api"):]
        self.calls.append((method, path))
        for staged_method, pattern, body in _ANSWERS:
            if staged_method == method and re.fullmatch(pattern, path):
                return _response(request, body)
        raise AssertionError(f"no canned answer staged for {method} {path}")


def _response(request: requests.PreparedRequest, body: Any) -> requests.Response:
    """Build a 200 response around a canned body.

    Args:
        request: The request being answered.
        body: Bytes to serve as a zip, or any JSON-encodable value.

    Returns:
        The response the client parses.
    """
    response = requests.Response()
    response.status_code = 200
    response.url = request.url or ""
    response.request = request
    response.encoding = "utf-8"
    if isinstance(body, bytes):
        response._content = body
        response.headers["content-type"] = "application/zip"
    else:
        response._content = json.dumps(body).encode("utf-8")
        response.headers["content-type"] = "application/json"
    # iter_content reads the raw stream unless the body is already consumed,
    # and a canned body has no raw stream behind it. The streaming export path
    # calls iter_content, so it reads the content set above instead. Written
    # with setattr because the flag is requests' own internal state rather
    # than part of its published Response type.
    setattr(response, "_content_consumed", True)
    return response


@pytest.fixture(autouse=True)
def export_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Name an approved export directory, which is host configuration.

    ``export_workroom_bundle`` refuses before any request when
    ``KAMIWAZA_AGENT_EXPORT_DIR`` is unset, because a host that named no
    directory approved no write. That refusal is correct and is not what this
    file is testing, so the directory is supplied and the platform path runs.
    """
    monkeypatch.setenv("KAMIWAZA_AGENT_EXPORT_DIR", str(tmp_path))


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Transport]:
    """Replace the transport and forbid a real socket for the test's duration."""
    stub = _Transport()

    def _send(
        _session: requests.Session, request: requests.PreparedRequest, **_kwargs: Any
    ) -> requests.Response:
        return stub.answer(request)

    def _no_network(_self: Any, address: Any) -> None:
        raise AssertionError(f"a workflow opened a socket to {address!r}")

    monkeypatch.setattr(requests.Session, "send", _send)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    yield stub


@pytest.fixture
def client() -> Iterator[KamiwazaClient]:
    """A real client pointed at a host nothing will connect to."""
    built = KamiwazaClient(base_url=_BASE_URL, api_key="test-token")
    yield built
    built.close()


def _host_payload(value: Any) -> Any:
    """Return a workflow's return value as the host would serialise it.

    The workflow contract allows plain data or an SDK model, and a host dumps
    a model and a dataclass before writing JSON. Everything else is left as it
    is, so a value no host can encode — bytes, a UUID, a datetime, a bare
    object — survives to fail :func:`json.dumps`.

    Args:
        value: A workflow's return value, or part of one.

    Returns:
        The same value with models and dataclasses reduced to plain data.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _host_payload(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {key: _host_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_host_payload(item) for item in value]
    return value


_BYTES_REPR = re.compile(r"^b['\"]")


def _bytes_like(value: Any, trail: str = "") -> list[str]:
    """Return the paths in a payload holding bytes, or the repr of bytes.

    ``str(b"...")`` is a string, so it survives JSON encoding and reaches an
    agent as ``"b'PK\\x03\\x04...'"``. That is the shape a workflow publishes
    when it stringifies whatever a service returned by default, so it is found
    by its prefix rather than by failing to encode.

    Args:
        value: The payload, already reduced by :func:`_host_payload`.
        trail: Path walked so far, for the failure message.

    Returns:
        One entry per offending value, naming where it sits.
    """
    if isinstance(value, bytes):
        return [f"{trail or 'payload'} is bytes"]
    if isinstance(value, str) and _BYTES_REPR.match(value):
        return [f"{trail or 'payload'} is the repr of bytes: {value[:40]!r}"]
    if isinstance(value, dict):
        return [
            found
            for key, item in value.items()
            for found in _bytes_like(item, f"{trail}[{key!r}]")
        ]
    if isinstance(value, list):
        return [
            found
            for index, item in enumerate(value)
            for found in _bytes_like(item, f"{trail}[{index}]")
        ]
    return []


def test_every_workflow_is_driven() -> None:
    """No workflow is left out of the harness, and none is skipped."""
    assert set(_ARGUMENTS) == set(WORKFLOWS)
    assert set(_FUNCTIONS) == set(WORKFLOWS)


def test_no_workflow_can_reach_a_network(transport: _Transport) -> None:
    """The socket guard is armed, so "no network call" is asserted not assumed.

    Args:
        transport: The fixture that installs the stub and the guard.
    """
    with pytest.raises(AssertionError):
        socket.socket().connect(("127.0.0.1", 9))
    assert not transport.calls


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_workflow_runs_against_a_real_client(
    name: str, client: KamiwazaClient, transport: _Transport
) -> None:
    """Every workflow runs end to end and publishes a payload a host can send.

    Args:
        name: Workflow being driven.
        client: The real client, transport stubbed.
        transport: The stub, read for the requests that reached it.
    """
    args, kwargs = _ARGUMENTS[name]

    result = _FUNCTIONS[name](client, *args, **kwargs)

    assert transport.calls, f"{name} reached no request at all"
    payload = _host_payload(result)
    offenders = _bytes_like(payload)
    assert not offenders, f"{name} published bytes: {'; '.join(offenders)}"
    json.dumps(payload)
