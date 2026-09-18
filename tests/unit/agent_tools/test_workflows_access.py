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

import pytest

from kamiwaza_sdk.agent_tools.workflows import (
    FederationEnrolment,
    GatePackageRef,
    grant_subject_access,
    install_and_bind_gate_package,
    pair_federation_and_allow_user,
    replace_gate_package,
)
from kamiwaza_sdk.schemas.authz import RelationshipTuple
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
    """Records the tuple a grant workflow writes.

    Attributes:
        written: Every tuple passed to :meth:`upsert_tuple`.
    """

    def __init__(self, calls: list[str]) -> None:
        """Start with no written tuples and the shared call log."""
        self.written: list[RelationshipTuple] = []
        self._calls = calls

    def upsert_tuple(self, relationship: RelationshipTuple) -> None:
        """Record the written tuple."""
        self._calls.append("authz.upsert_tuple")
        self.written.append(relationship)


class FakeFederations:
    """Stands in for the federations API, returning a real ``Federation``."""

    def __init__(self, calls: list[str]) -> None:
        """Keep the shared call log."""
        self._calls = calls

    def pair(
        self, *, name: str, role: str, remote_url: str | None = None
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


def test_grant_subject_access_returns_the_grants_it_read() -> None:
    """The payload carries the grant list, not the grants accessor.

    ``client.subjects.grants(username)`` only builds ``SubjectGrantsAPI``;
    returning it published a local object where the agent expects grants and
    made the module's read-back promise false.
    """
    client = FakeClient([_grant("reader"), _grant("owner")])

    result = grant_subject_access(
        client, "member-1", "reader", "dataset", "ds-1"
    )

    assert result["grants"] == [
        {"object_namespace": "dataset", "object_id": "ds-1", "relation": "reader"},
        {"object_namespace": "dataset", "object_id": "ds-1", "relation": "owner"},
    ]
    assert json.loads(json.dumps(result)) == result
    assert "subjects.grants.list" in client.calls
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


def test_pair_federation_and_allow_user_returns_the_grants_it_read() -> None:
    """The federation payload carries the grant list and stays JSON data."""
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
    assert result["federation"] == "fed-1"
    assert result["subject"] == "member-1"


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
    """The replace result and every binding travel as data."""
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
