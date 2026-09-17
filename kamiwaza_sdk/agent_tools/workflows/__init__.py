"""High-leverage workflow tools over the platform client.

The chains Kamiwaza owns, published as single operations so the common paths
need no discovery at all. An agent that has to find, deploy, wait, and then look
up an endpoint spends four calls and three chances to get the order wrong; the
workflow spends one and returns the endpoint.

Every workflow obeys the same contract, per the MCP server's FR-014 to FR-018,
and :data:`WORKFLOWS` records it as data so the contract can be asserted rather
than reviewed:

* **a terminal artifact** — the final usable thing, not a handle to chase;
* **at most one polling step**, bounded by a timeout the caller can set. Two
  stacked waits means two tools;
* **at most one approval-bearing step**, so a member approving once is not
  approving a chain;
* **idempotence stated plainly**, including when it is absent;
* **no wrapped single destructive act.** Deleting, rotating a credential and
  uninstalling stay explicit single calls — nothing irreversible hides inside a
  convenience.

Grouped by domain rather than kept in one module, because a deployment workflow
and a gate-package workflow share no data and no helpers; one file holding both
is a file with two responsibilities.

Protocol-neutral: a workflow takes a client and returns plain data or an SDK
model. Nothing here imports an MCP library or names a wire field.

**A workflow's parameters are its published input schema**, which is why four
of them carry more than the four arguments a complexity tool prefers:
``grant_subject_access`` takes seven, ``enclave_ingest`` six,
``rag_query`` and ``preflight_and_deploy_model`` five. The MCP server
synthesises each tool's signature from these parameters, so the names here are
the flat fields an agent fills — measured on a live deployment,
``grant_subject_access`` publishes ``username``, ``relation``, ``object_type``,
``object_id``, ``subject_type`` and ``attributes``. Grouping them into a value
object would nest the published schema and make an agent construct an object
to call a tool, which is the opposite of FR-006's requirement for a shape an
agent can fill. The finding is accepted rather than fixed, and it is a fact
about the surface rather than about this code's readability.
"""

from __future__ import annotations

from ._contract import (
    WORKFLOWS,
    DeploymentOutcome,
    Refusal,
    WorkflowSpec,
    register,
)
from .access import (
    FederationEnrolment,
    GatePackageRef,
    grant_subject_access,
    install_and_bind_gate_package,
    pair_federation_and_allow_user,
    replace_gate_package,
)
from .collaboration import (
    AppRequest,
    WorkroomDraft,
    create_workroom_and_enter,
    deploy_app_from_garden,
    export_workroom_bundle,
)
from .data import (
    DatasetTarget,
    complete_dataset_ingestion,
    enclave_ingest,
    ingest_dataset_and_index,
    prepare_dataset_ingestion,
    rag_query,
)
from .models import (
    deploy_and_connect_model,
    diagnose_deployment,
    find_and_deploy_model,
    preflight_and_deploy_model,
    retire_deployment,
)

__all__ = [
    "WORKFLOWS",
    "AppRequest",
    "DatasetTarget",
    "DeploymentOutcome",
    "FederationEnrolment",
    "GatePackageRef",
    "Refusal",
    "WorkflowSpec",
    "WorkroomDraft",
    "complete_dataset_ingestion",
    "create_workroom_and_enter",
    "deploy_and_connect_model",
    "deploy_app_from_garden",
    "diagnose_deployment",
    "enclave_ingest",
    "export_workroom_bundle",
    "find_and_deploy_model",
    "grant_subject_access",
    "ingest_dataset_and_index",
    "install_and_bind_gate_package",
    "pair_federation_and_allow_user",
    "preflight_and_deploy_model",
    "prepare_dataset_ingestion",
    "rag_query",
    "register",
    "replace_gate_package",
    "retire_deployment",
]
