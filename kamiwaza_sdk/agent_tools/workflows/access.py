"""Federation, subject, and gate-package workflows.

Every workflow here reads the platform back after it writes, and carries the
read as JSON data rather than as an SDK object. Access control that reports
success without confirming the resulting grant is the failure mode these
exist to catch: an agent told "granted" moves on, and the member still
cannot reach anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kamiwaza_sdk.schemas.authz import (
    CheckRequest,
    ObjectModel,
    RelationshipTuple,
    SubjectModel,
)

from ._contract import WorkflowSpec, register

__all__ = [
    "GatePackageRef",
    "grant_subject_access",
    "install_and_bind_gate_package",
    "pair_federation_and_allow_user",
    "replace_gate_package",
]


def _as_data(value: Any) -> Any:
    """Return a read-back value as JSON data.

    The platform's read methods return pydantic models, and a workflow payload
    crosses a JSON transport to reach the agent. Dumping here keeps the
    published payload serialisable without asking every caller to know which
    fields are models.

    Args:
        value: A model, a list of models, or plain data.

    Returns:
        The same value as dicts, lists, and scalars.
    """
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped: Any = dump(mode="json")
        return dumped
    if isinstance(value, list):
        return [_as_data(item) for item in value]
    return value


def _grants_of(client: Any, username: str) -> Any:
    """Read a subject's grants back as JSON data.

    ``client.subjects.grants(username)`` only builds a subject-scoped grants
    accessor; ``.list()`` is the call that performs the GET. Returning the
    accessor put a local object where the agent expects grants, so no workflow
    using it actually confirmed the write.

    Args:
        client: The platform client.
        username: Subject whose grants to read.

    Returns:
        The subject's grants, as a list of dicts.
    """
    return _as_data(client.subjects.grants(username).list())


# The hash is a required field rather than an optional argument: a gate
# package decides whether other code may run, so identifying one by name
# alone would let the index decide what executes.
@dataclass(frozen=True, slots=True)
class GatePackageRef:
    """A gate package, pinned.

    Attributes:
        name: Installed package name; reads the binding back.
        spec: Package specifier.
        hash_digest: Hash the package must match.
        index_url: Package index, when not the default.
    """

    name: str
    spec: str
    hash_digest: str
    index_url: str | None = None


@register(
    WorkflowSpec(
        name="install_and_bind_gate_package",
        summary="Install a hash-pinned gate package and read back its binding.",
        terminal_artifact="The installed package's state, read back.",
        polling_step=None,
        approval_step="Installing the package.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Installing POSTs a new package record, so a second call "
            "conflicts rather than reinstalling. Read the binding first."
        ),
    )
)
def install_and_bind_gate_package(
    client: Any, package: GatePackageRef
) -> dict[str, Any]:
    """Install a gate package by hash and confirm the resulting binding.

    Args:
        client: The platform client.
        package: The pinned package to install.

    Returns:
        Mapping with the ``install`` result and the ``binding`` read back.
    """
    # Installing without confirming leaves an agent believing an unverified
    # package is bound. ``install`` carries the whole
    # ``GatePackageInstallResult``: that model has no single status field, and
    # stringifying it published a model repr instead of data.
    installed = client.gates.packages.install(
        package.spec, package.hash_digest, index_url=package.index_url
    )
    return {
        "install": _as_data(installed),
        "binding": _as_data(client.gates.packages.get(package.name)),
    }


@register(
    WorkflowSpec(
        name="replace_gate_package",
        summary="Replace an installed gate package and report the bindings.",
        terminal_artifact="The new package hash and the first page of bindings.",
        polling_step=None,
        approval_step="Replacing the package.",
        idempotent=False,
        reads_only=False,
        destructive=True,
        not_idempotent_because=(
            "Replacing re-resolves every binding, and a failed replace can "
            "leave them pointing at the previous package. Read the binding "
            "report first."
        ),
    )
)
def replace_gate_package(client: Any, package: GatePackageRef) -> dict[str, Any]:
    """Replace a gate package and report the bindings the platform lists.

    Args:
        client: The platform client.
        package: The pinned replacement package.

    Returns:
        Mapping with ``replaced``, the new ``hash``, the ``result`` as data,
        and ``bindings``, one page whose ``total`` counts them all.
    """
    # ``GatePackagesAPI.list`` takes no page argument, so ``bindings`` is the
    # first page the platform serves (20 per page by default) and ``total``
    # is how many exist. Calling this a report of every binding overstated a
    # page whenever a cluster held more.
    replaced = client.gates.packages.replace(
        package.name,
        package.spec,
        package.hash_digest,
        index_url=package.index_url,
    )
    return {
        "replaced": package.name,
        "hash": package.hash_digest,
        "result": _as_data(replaced),
        "bindings": _as_data(client.gates.packages.list()),
    }


@dataclass(frozen=True, slots=True)
class FederationEnrolment:
    """A federation pairing and the user to enrol across it.

    Attributes:
        name: Federation name.
        role: This side's role in the pairing.
        username: User to enrol.
        remote_url: Remote federation URL, when the role needs one.
        attributes: Attributes to set on the subject.
        preshared_key: Key both sides of the pairing must share.
    """

    name: str
    role: str
    username: str
    remote_url: str | None = None
    attributes: dict[str, Any] | None = None
    preshared_key: str | None = None


@register(
    WorkflowSpec(
        name="pair_federation_and_allow_user",
        summary="Pair a federation and enrol one user across it.",
        terminal_artifact="The federation id, the subject, and its grants.",
        polling_step=None,
        approval_step="Pairing the federation.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Pairing POSTs a new federation record on every call, so a "
            "second call adds a second pairing. Read the federation list "
            "before calling again."
        ),
    )
)
def pair_federation_and_allow_user(
    client: Any, enrolment: FederationEnrolment
) -> dict[str, Any]:
    """Pair a federation, enrol a user, and read that user's grants.

    Writes no grant: the grants read back are the ones the subject already
    holds, and ``grant_subject_access`` adds one. Omitting ``preshared_key``
    mints a UUID4 nothing returns, so a two-sided pairing must supply it.

    Args:
        client: The platform client.
        enrolment: The pairing and the user to enrol.

    Returns:
        Mapping with ``federation``, ``subject`` and the ``grants`` the
        subject holds.
    """
    federation = client.federations.pair(
        name=enrolment.name,
        role=enrolment.role,
        remote_url=enrolment.remote_url,
        preshared_key=enrolment.preshared_key,
    )
    subject = client.subjects.upsert(
        enrolment.username, attributes=enrolment.attributes or {}
    )
    return {
        "federation": str(getattr(federation, "id", federation)),
        "subject": getattr(subject, "username", enrolment.username),
        "grants": _grants_of(client, enrolment.username),
    }


@register(
    WorkflowSpec(
        name="grant_subject_access",
        summary="Create or update a subject, grant access, and read it back.",
        terminal_artifact="The grant written and the check that confirms it.",
        polling_step=None,
        approval_step="Creating the grant.",
        idempotent=True,
        reads_only=False,
        destructive=False,
    )
)
def grant_subject_access(
    client: Any,
    username: str,
    relation: str,
    object_type: str,
    object_id: str,
    *,
    subject_type: str = "user",
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Grant one subject access, then confirm it for that same subject.

    Args:
        client: The platform client.
        username: Subject to upsert and grant to.
        relation: Relation to grant, such as ``reader`` or ``owner``.
        object_type: Kind of thing being granted on.
        object_id: Which thing of that kind.
        subject_type: Kind of subject, for the platform's own namespacing.
        attributes: Attributes to set on the subject.

    Returns:
        Mapping with ``subject``, the ``grant`` written, and the ``check``
        of that same grant.
    """
    subject = client.subjects.upsert(username, attributes=attributes or {})
    # The tuple routes key a subject by the namespace and id in the body.
    # ``subjects.grants(username).list()`` reads a different route, which
    # resolves its path segment to a Keycloak id first, so a username written
    # here need not be the id that list is keyed on. The check below reuses
    # this tuple's own subject and object, so the confirmation names the
    # identity the write named either way the platform resolves the string.
    granted = RelationshipTuple(
        subject=SubjectModel(namespace=subject_type, id=username),
        relation=relation,
        object=ObjectModel(namespace=object_type, id=object_id),
    )
    client.authz.upsert_tuple(granted)
    decision = client.authz.check_access(
        CheckRequest(
            subject=granted.subject, relation=relation, object=granted.object
        )
    )
    return {
        "subject": getattr(subject, "username", username),
        "grant": _as_data(granted),
        "check": _as_data(decision),
    }
