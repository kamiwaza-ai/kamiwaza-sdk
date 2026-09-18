"""What the access workflows publish, asserted against the real models.

These tests exist because the suite's shared ``Recorder`` fake answers any
attribute with a canned value, so a workflow that called a factory and never
read through it still looked right. The fakes here are shaped like the
services they stand in for: ``subjects.grants(username)`` returns an accessor
whose ``list()`` is the GET, and the read returns real
:class:`~kamiwaza_sdk.schemas.federation.Grant` models. A workflow that hands
back the accessor instead of the grants fails both assertions below - the
payload is not JSON data, and it is not the grant list.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import pytest
import requests

from kamiwaza_sdk.agent_tools.descriptors import derive_hints
from kamiwaza_sdk.agent_tools.workflows import (
    WORKFLOWS,
    FederationEnrolment,
    GatePackageRef,
    grant_subject_access,
    install_and_bind_gate_package,
    pair_federation_and_allow_user,
    replace_gate_package,
)
from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.schemas.authz import CheckRequest, CheckResponse, RelationshipTuple
from kamiwaza_sdk.schemas.federation import Federation, Grant, Subject
from kamiwaza_sdk.schemas.gate_packages import (
    GatePackageInstallResult,
    GatePackageList,
    GatePackageState,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 3, 1, 12, 30, tzinfo=timezone.utc)


def _grant(relation: str = "reader") -> Grant:
    """Build one grant the way the platform returns it."""
    return Grant(object_namespace="dataset", object_id="ds-1", relation=relation)


class FakeGrantsAccessor:
    """Stands in for ``SubjectGrantsAPI``: the read is ``list()``, not the call.

    Attributes:
        calls: Shared call log, appended to on every read.
    """

    def __init__(self, grants: list[Grant], calls: list[str]) -> None:
        """Record the grants this subject holds and the shared call log."""
        self._grants = grants
        self.calls = calls

    def list(self) -> list[Grant]:
        """Return the subject's grants, as ``SubjectGrantsAPI.list`` does."""
        self.calls.append("subjects.grants.list")
        return list(self._grants)


class FakeSubjects:
    """Stands in for ``SubjectsAPI`` with the grants sub-resource intact."""

    def __init__(self, grants: list[Grant], calls: list[str]) -> None:
        """Record the grants the read will return and the shared call log."""
        self._grants = grants
        self._calls = calls

    def upsert(
        self, username: str, *, attributes: dict[str, Any] | None = None
    ) -> Subject:
        """Return a real ``Subject``, as the platform's upsert does."""
        self._calls.append("subjects.upsert")
        return Subject(
            id="subject-1",
            username=username,
            attributes=attributes or {},
            created_at=_NOW,
        )

    def grants(self, username: str) -> FakeGrantsAccessor:
        """Return the grants accessor, making no request of its own."""
        self._calls.append("subjects.grants")
        return FakeGrantsAccessor(self._grants, self._calls)


class FakeAuthz:
    """Records the tuple a grant workflow writes and what it checked back.

    Attributes:
        written: Every tuple passed to :meth:`upsert_tuple`.
        checked: Every request passed to :meth:`check_access`.
    """

    def __init__(self, calls: list[str]) -> None:
        """Start with no written tuples and the shared call log."""
        self.written: list[RelationshipTuple] = []
        self.checked: list[CheckRequest] = []
        self._calls = calls

    def upsert_tuple(self, relationship: RelationshipTuple) -> None:
        """Record the written tuple."""
        self._calls.append("authz.upsert_tuple")
        self.written.append(relationship)

    def check_access(self, request: CheckRequest) -> CheckResponse:
        """Record the check and answer it, as the authz API does."""
        self._calls.append("authz.check_access")
        self.checked.append(request)
        return CheckResponse(allow=True, decision_id="d-1", reason="tuple_found")


class FakeFederations:
    """Stands in for the federations API, returning a real ``Federation``."""

    def __init__(self, calls: list[str]) -> None:
        """Keep the shared call log."""
        self._calls = calls

    def pair(
        self,
        *,
        name: str,
        role: str,
        remote_url: str | None = None,
        preshared_key: str | None = None,
    ) -> Federation:
        """Return the paired federation record."""
        self._calls.append("federations.pair")
        return Federation(id="fed-1", status="active", remote_cluster_name=name)


def _package_state(name: str = "policy") -> GatePackageState:
    """Build one installed gate-package state record."""
    return GatePackageState(
        name=name,
        package_spec=f"{name}==1.0.0",
        version="1.0.0",
        hash_digest="sha256:abc",
        installed_at=_NOW,
        installed_by="kc-subject-1",
        classpaths=[f"{name}.Gate"],
    )


class FakeGatePackages:
    """Stands in for ``GatePackagesAPI``, returning the real response models."""

    def __init__(self, calls: list[str]) -> None:
        """Keep the shared call log."""
        self._calls = calls

    def install(
        self, package_spec: str, hash_digest: str, *, index_url: str | None = None
    ) -> GatePackageInstallResult:
        """Return the install result the service returns."""
        self._calls.append("gates.packages.install")
        return GatePackageInstallResult(
            package=_package_state(),
            install_duration_seconds=1.5,
            audit_event_id="audit-1",
        )

    def replace(
        self,
        name: str,
        package_spec: str,
        hash_digest: str,
        *,
        index_url: str | None = None,
    ) -> GatePackageInstallResult:
        """Return the replace result the service returns."""
        self._calls.append("gates.packages.replace")
        return GatePackageInstallResult(
            package=_package_state(name),
            install_duration_seconds=2.0,
            audit_event_id="audit-2",
        )

    def get(self, name: str) -> GatePackageState:
        """Return the package's state record."""
        self._calls.append("gates.packages.get")
        return _package_state(name)

    def list(self) -> GatePackageList:
        """Return every installed package."""
        self._calls.append("gates.packages.list")
        return GatePackageList(items=[_package_state()], total=1)


class FakeGates:
    """Carries the gate-packages sub-API the workflows reach through.

    Attributes:
        packages: The gate-packages sub-API.
    """

    def __init__(self, calls: list[str]) -> None:
        """Build the sub-API against the shared call log."""
        self.packages = FakeGatePackages(calls)


class FakeClient:
    """A platform client with the services the access workflows touch.

    Attributes:
        calls: Every service call the workflows made, in order.
        subjects: The subjects API.
        authz: The authorization API.
        federations: The federations API.
        gates: The gates API.
    """

    def __init__(self, grants: list[Grant] | None = None) -> None:
        """Wire the fake services against one shared call log."""
        self.calls: list[str] = []
        self.subjects = FakeSubjects(grants or [_grant()], self.calls)
        self.authz = FakeAuthz(self.calls)
        self.federations = FakeFederations(self.calls)
        self.gates = FakeGates(self.calls)


def test_grant_subject_access_reports_the_check_for_the_tuple_it_wrote() -> None:
    """The payload carries the written grant and the check that confirms it.

    The confirmation reuses the tuple's own subject and object, so what the
    workflow reports is a read of the identity it wrote rather than of a
    username another route may resolve differently.
    """
    client = FakeClient([_grant("reader"), _grant("owner")])

    result = grant_subject_access(
        client, "member-1", "reader", "dataset", "ds-1"
    )

    assert len(client.authz.checked) == 1
    checked = client.authz.checked[0]
    assert checked.subject == client.authz.written[0].subject
    assert checked.object == client.authz.written[0].object
    assert checked.relation == "reader"
    assert result["grant"]["subject"] == {"namespace": "user", "id": "member-1"}
    assert result["check"]["allow"] is True
    assert json.loads(json.dumps(result)) == result
    assert result["subject"] == "member-1"


def test_grant_subject_access_writes_the_tuple_it_was_asked_for() -> None:
    """The one written tuple names the subject, relation, and object given."""
    client = FakeClient()

    grant_subject_access(
        client,
        "member-1",
        "owner",
        "model",
        "m-9",
        subject_type="group",
    )

    assert len(client.authz.written) == 1
    written = client.authz.written[0]
    assert written.subject.namespace == "group"
    assert written.subject.id == "member-1"
    assert written.relation == "owner"
    assert written.object.namespace == "model"
    assert written.object.id == "m-9"


def test_pair_federation_and_allow_user_writes_no_grant_of_its_own() -> None:
    """It pairs, upserts the subject, and reads the grants already there."""
    client = FakeClient([_grant("reader")])
    enrolment = FederationEnrolment(
        name="partner", role="sender", username="member-1"
    )

    result = pair_federation_and_allow_user(client, enrolment)

    assert result["grants"] == [
        {"object_namespace": "dataset", "object_id": "ds-1", "relation": "reader"}
    ]
    assert json.loads(json.dumps(result)) == result
    assert client.calls == [
        "federations.pair",
        "subjects.upsert",
        "subjects.grants",
        "subjects.grants.list",
    ]
    assert client.authz.written == []
    assert result["federation"] == "fed-1"
    assert result["subject"] == "member-1"


def test_pair_federation_declares_the_idempotence_its_pairing_has() -> None:
    """``federations.pair`` derives ``idempotent=False``; the spec must match.

    A workflow that declares more than the operation it wraps is what turns
    ``idempotent`` into ``safe_to_retry`` on a host, and each retry POSTs
    another federation record.
    """
    spec = WORKFLOWS["pair_federation_and_allow_user"]
    derived = derive_hints("federations.pair", "pair")

    assert derived is not None
    assert derived.idempotent is False
    assert spec.idempotent is derived.idempotent
    assert spec.not_idempotent_because


def test_gate_package_install_payload_is_json_data() -> None:
    """Install and binding travel as data, not as model reprs.

    ``GatePackagesAPI.install`` returns ``GatePackageInstallResult``, which has
    no ``status`` field, so reporting ``getattr(result, "status", None) or
    str(result)`` published a pydantic repr. ``installed_at`` is a datetime,
    which also has to be dumped before the payload can cross JSON.
    """
    client = FakeClient()
    package = GatePackageRef(
        name="policy", spec="policy==1.0.0", hash_digest="sha256:abc"
    )

    result = install_and_bind_gate_package(client, package)

    assert json.loads(json.dumps(result)) == result
    assert result["install"]["package"]["version"] == "1.0.0"
    assert result["install"]["audit_event_id"] == "audit-1"
    assert result["binding"]["classpaths"] == ["policy.Gate"]
    assert result["binding"]["installed_at"] == "2026-03-01T12:30:00Z"


def test_gate_package_replace_payload_is_json_data() -> None:
    """The replace result and the bindings page travel as data."""
    client = FakeClient()
    package = GatePackageRef(
        name="policy", spec="policy==2.0.0", hash_digest="sha256:def"
    )

    result = replace_gate_package(client, package)

    assert json.loads(json.dumps(result)) == result
    assert result["replaced"] == "policy"
    assert result["hash"] == "sha256:def"
    assert result["result"]["install_duration_seconds"] == 2.0
    assert result["bindings"]["items"][0]["name"] == "policy"


def _http_response(
    request: requests.PreparedRequest, payload: Any, status: int = 200
) -> requests.Response:
    """Build the response the stubbed transport hands back to the client."""
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    response.headers["Content-Type"] = "application/json"
    response.request = request
    response.url = request.url or ""
    return response


class StubTransport:
    """Answers ``requests.Session.send`` from a route table, recording calls.

    A service-level fake cannot see which URL a method builds or what it puts
    in the body, which is where a write and its read-back stop naming the same
    subject. Patching the transport runs the real service methods and the real
    client, so the recorded requests are what the platform would receive.

    Attributes:
        seen: Every ``(method, path, body)`` sent, in order.
    """

    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        """Keep the route table and start with nothing recorded."""
        self._routes = routes
        self.seen: list[tuple[str, str, Any]] = []

    def __call__(
        self, request: requests.PreparedRequest, **kwargs: Any
    ) -> requests.Response:
        """Record one request and answer it, or 404 a path with no route."""
        path = urlsplit(request.url or "").path
        raw = request.body
        body = json.loads(raw) if isinstance(raw, (str, bytes)) else None
        method = request.method or ""
        self.seen.append((method, path, body))
        routed = self._routes.get((method, path))
        if routed is None:
            return _http_response(request, {"detail": f"no route for {path}"}, 404)
        return _http_response(request, routed)

    def paths(self) -> list[str]:
        """Return the paths requested, in order."""
        return [path for _, path, _ in self.seen]

    def body(self, method: str, path: str) -> Any:
        """Return the body sent to one route, or ``None`` when never called."""
        for sent_method, sent_path, body in self.seen:
            if sent_method == method and sent_path == path:
                return body
        return None


# The Keycloak id the subject routes resolve `member-1` to. Deliberately not
# the username: the tuple routes key a subject by the id in the body, so a
# read-back keyed on the username reads a different subject on any platform
# that resolves the path segment first.
_SUBJECT_BODY = {
    "id": "kc-9f3b",
    "username": "member-1",
    "attributes": {},
    "created_at": "2026-03-01T12:30:00Z",
}


def _real_client(
    monkeypatch: pytest.MonkeyPatch, routes: dict[tuple[str, str], Any]
) -> tuple[KamiwazaClient, StubTransport]:
    """Return a real client whose transport is the given route table."""
    transport = StubTransport(routes)
    monkeypatch.setattr(requests.Session, "send", transport)
    client = KamiwazaClient(base_url="http://localhost:7777/api", api_key="test-token")
    return client, transport


def test_grant_subject_access_checks_the_subject_id_it_wrote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read-back names the subject the write named, on the wire.

    ``subjects.grants(username).list()`` reads
    ``GET /authz/subjects/{id_or_username}/grants``, which resolves its path
    segment to a Keycloak id, while ``POST /auth/tuples`` keys the subject by
    the id in the body. Reading the grant back through that route reported a
    list that need not hold the tuple just written.
    """
    client, transport = _real_client(
        monkeypatch,
        {
            ("PUT", "/api/authz/subjects/member-1"): _SUBJECT_BODY,
            ("POST", "/api/auth/tuples"): {},
            ("POST", "/api/auth/check"): {
                "allow": True,
                "decision_id": "d-1",
                "reason": "tuple_found",
            },
        },
    )

    result = grant_subject_access(client, "member-1", "reader", "dataset", "ds-1")

    written = transport.body("POST", "/api/auth/tuples")
    checked = transport.body("POST", "/api/auth/check")
    assert written == {
        "subject": {"namespace": "user", "id": "member-1"},
        "relation": "reader",
        "object": {"namespace": "dataset", "id": "ds-1"},
    }
    assert checked["subject"] == written["subject"]
    assert checked["object"] == written["object"]
    assert checked["relation"] == written["relation"]
    assert "/api/authz/subjects/member-1/grants" not in transport.paths()
    assert result["check"] == {
        "allow": True,
        "decision_id": "d-1",
        "reason": "tuple_found",
    }


def test_grant_subject_access_reports_a_denied_check_as_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write the platform will not confirm must not read as a grant."""
    client, _ = _real_client(
        monkeypatch,
        {
            ("PUT", "/api/authz/subjects/member-1"): _SUBJECT_BODY,
            ("POST", "/api/auth/tuples"): {},
            ("POST", "/api/auth/check"): {
                "allow": False,
                "decision_id": "d-2",
                "reason": "no_tuple",
            },
        },
    )

    result = grant_subject_access(client, "member-1", "reader", "dataset", "ds-1")

    assert result["check"]["allow"] is False
    assert result["check"]["reason"] == "no_tuple"


def test_pair_federation_sends_the_preshared_key_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key reaches the pairing body, and the grants read are the existing ones.

    Without a way to supply it, ``pair`` minted a UUID4 that no caller could
    read, so the second cluster in a pairing could never be given the same
    value.
    """
    client, transport = _real_client(
        monkeypatch,
        {
            ("POST", "/api/cluster/federations"): {
                "id": "fed-1",
                "status": "waiting",
                "remote_cluster_name": "partner",
            },
            ("PUT", "/api/authz/subjects/member-1"): _SUBJECT_BODY,
            ("GET", "/api/authz/subjects/member-1/grants"): [
                {
                    "object_namespace": "dataset",
                    "object_id": "ds-1",
                    "relation": "reader",
                }
            ],
        },
    )
    enrolment = FederationEnrolment(
        name="partner",
        role="receiver",
        username="member-1",
        preshared_key="psk-shared",
    )

    result = pair_federation_and_allow_user(client, enrolment)

    created = transport.body("POST", "/api/cluster/federations")
    assert created["preshared_key"] == "psk-shared"
    assert "/api/auth/tuples" not in transport.paths()
    assert result["grants"] == [
        {"object_namespace": "dataset", "object_id": "ds-1", "relation": "reader"}
    ]
    assert result["federation"] == "fed-1"


def test_replace_gate_package_bindings_are_one_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bindings`` is the page ``list()`` returned, and ``total`` counts the rest."""
    package_body = {
        "name": "policy",
        "package_spec": "policy==2.0.0",
        "version": "2.0.0",
        "hash_digest": "sha256:def",
        "installed_at": "2026-03-01T12:30:00Z",
        "installed_by": "kc-9f3b",
        "classpaths": ["policy.Gate"],
    }
    client, _ = _real_client(
        monkeypatch,
        {
            ("PUT", "/api/authz/gate-packages/policy"): {
                "package": package_body,
                "install_duration_seconds": 2.0,
                "audit_event_id": "audit-2",
            },
            ("GET", "/api/authz/gate-packages"): {
                "items": [package_body],
                "total": 25,
                "page": 1,
                "per_page": 20,
            },
        },
    )
    package = GatePackageRef(
        name="policy", spec="policy==2.0.0", hash_digest="sha256:def"
    )

    result = replace_gate_package(client, package)

    assert len(result["bindings"]["items"]) == 1
    assert result["bindings"]["total"] == 25
    assert result["bindings"]["per_page"] == 20
