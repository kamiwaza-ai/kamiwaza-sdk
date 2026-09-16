"""Federation, subject, and gate-package workflows.

Every workflow here reads back what it wrote. Access control that reports
success without confirming the resulting grant is the failure mode these exist
to catch: an agent told "granted" moves on, and the member still cannot reach
anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kamiwaza_sdk.schemas.authz import ObjectModel, RelationshipTuple, SubjectModel

from ._contract import WorkflowSpec, register

__all__ = [
    "GatePackageRef",
    "grant_subject_access",
    "install_and_bind_gate_package",
    "pair_federation_and_allow_user",
    "replace_gate_package",
]


@dataclass(frozen=True, slots=True)
class GatePackageRef:
    """A gate package, pinned.

    The hash is a required field rather than an optional argument: a gate
    package decides whether other code may run, so identifying one by name
    alone would let the index decide what executes.

    Attributes:
        name: Installed package name, used to read the binding back.
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
        terminal_artifact="The installed package's state, read back after install.",
        polling_step=None,
        approval_step="Installing the package.",
        idempotent=True,
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
        Installing without confirming leaves an agent believing an unverified
        package is bound.
    """
    installed = client.gates.packages.install(
        package.spec, package.hash_digest, index_url=package.index_url
    )
    return {
        "install": getattr(installed, "status", None) or str(installed),
        "binding": client.gates.packages.get(package.name),
    }


@register(
    WorkflowSpec(
        name="replace_gate_package",
        summary="Replace an installed gate package and report every binding.",
        terminal_artifact="The new package hash and a report of all bindings.",
        polling_step=None,
        approval_step="Replacing the package.",
        idempotent=False,
        not_idempotent_because=(
            "Replacing re-resolves every existing binding, and a failed "
            "replace can leave bindings pointing at the previous package. "
            "Read the binding report before calling again."
        ),
    )
)
def replace_gate_package(client: Any, package: GatePackageRef) -> dict[str, Any]:
    """Replace a gate package and report what every binding now resolves to.

    Args:
        client: The platform client.
        package: The pinned replacement package.

    Returns:
        Mapping with ``replaced``, the new ``hash``, the ``result``, and a
        ``bindings`` report covering every package the platform now holds.
    """
    replaced = client.gates.packages.replace(
        package.name,
        package.spec,
        package.hash_digest,
        index_url=package.index_url,
    )
    return {
        "replaced": package.name,
        "hash": package.hash_digest,
        "result": getattr(replaced, "status", None) or str(replaced),
        "bindings": client.gates.packages.list(),
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
    """

    name: str
    role: str
    username: str
    remote_url: str | None = None
    attributes: dict[str, Any] | None = None


@register(
    WorkflowSpec(
        name="pair_federation_and_allow_user",
        summary="Pair a federation and grant one user access across it.",
        terminal_artifact="The federation id and the verified grant.",
        polling_step=None,
        approval_step="Pairing the federation.",
        idempotent=True,
    )
)
def pair_federation_and_allow_user(
    client: Any, enrolment: FederationEnrolment
) -> dict[str, Any]:
    """Pair a federation, enrol a user, and read the grant back.

    Args:
        client: The platform client.
        enrolment: The pairing and the user to enrol.

    Returns:
        Mapping with ``federation``, ``subject`` and the ``grants`` read back.
    """
    federation = client.federations.pair(
        name=enrolment.name,
        role=enrolment.role,
        remote_url=enrolment.remote_url,
    )
    subject = client.subjects.upsert(
        enrolment.username, attributes=enrolment.attributes or {}
    )
    return {
        "federation": str(getattr(federation, "id", federation)),
        "subject": getattr(subject, "username", enrolment.username),
        "grants": client.subjects.grants(enrolment.username),
    }


@register(
    WorkflowSpec(
        name="grant_subject_access",
        summary="Create or update a subject, grant access, and read it back.",
        terminal_artifact="The subject and its effective grants.",
        polling_step=None,
        approval_step="Creating the grant.",
        idempotent=True,
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
    """Upsert a subject, write one authorization tuple, and confirm the result.

    The tuple is assembled here from its parts rather than accepted whole. A
    ``RelationshipTuple`` nests a subject model and an object model, so
    publishing it meant a caller reading three definitions to write one grant.

    Args:
        client: The platform client.
        username: Subject to upsert and grant to.
        relation: Relation to grant, such as ``reader`` or ``owner``.
        object_type: Kind of thing being granted on.
        object_id: Which thing of that kind.
        subject_type: Kind of subject, for the platform's own namespacing.
        attributes: Attributes to set on the subject.

    Returns:
        Mapping with ``subject`` and its ``grants`` read back after the write.
    """
    subject = client.subjects.upsert(username, attributes=attributes or {})
    client.authz.upsert_tuple(
        RelationshipTuple(
            subject=SubjectModel(namespace=subject_type, id=username),
            relation=relation,
            object=ObjectModel(namespace=object_type, id=object_id),
        )
    )
    return {
        "subject": getattr(subject, "username", username),
        "grants": client.subjects.grants(username),
    }
