"""Static contract and resolution for the owned shared-IdP scenario."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from kamiwaza_sdk.validation.applicability import ApplicableTarget
from kamiwaza_sdk.validation.models import (
    FactMatcher,
    ResolvedScenario,
    ScenarioDescriptor,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.registry import model_digest

FEDERATION_PROVIDER_ID = "sdk.federation.shared-idp"
FEDERATION_PROVIDER_REVISION = "sdk.federation.shared-idp@v1"
FEDERATION_SCENARIO_ID = "sdk.federation.shared-idp/v1"

# These are the stable public case IDs for the nine-case inventory currently
# exercised by the legacy required_federation_edge suite.  The provider owns
# these IDs; pytest node names remain an internal SDK implementation detail.
FEDERATION_CASE_IDS = (
    "retrieval-clearance-u",
    "retrieval-clearance-s",
    "retrieval-clearance-ts",
    "retrieval-invalid-tenant-missing-canonical",
    "retrieval-invalid-tenant-legacy-only",
    "retrieval-invalid-tenant-canonical-nondefault",
    "dataset-list-authorized-fixture",
    "job-reaches-receiver-marker",
    "unonboarded-user-rejected",
)

SHARED_REALM_CLIENT_ID = "kamiwaza-shared-cli"
SHARED_REALM_ADMIN_PASSWORD_REF = "shared-idp-admin-password"
SHARED_REALM_PERSONA_PASSWORD_REF = "shared-idp-persona-password"


def scenario_descriptor() -> ScenarioDescriptor:
    """Describe one required shared-IdP mesh-edge scenario."""

    return ScenarioDescriptor(
        scenario_id=FEDERATION_SCENARIO_ID,
        provider_id=FEDERATION_PROVIDER_ID,
        protocol_version="v1",
        target_scope="mesh_edge",
        minimum_level="smoke",
        capability_ids=(
            "federation.shared-idp",
            "federation.gated-retrieval",
            "federation.remote-jobs",
        ),
        applies_when=(
            FactMatcher(
                path=("edge", "identity_mode"),
                operator="eq",
                value="shared_idp",
            ),
        ),
        requires=("cluster-api", "ownership-key"),
        fixture_modes=("owned",),
        case_ids=FEDERATION_CASE_IDS,
    )


def resolve_candidates(
    profile: ValidationProfile,
    candidates: Sequence[ApplicableTarget],
    *,
    explicit: bool,
) -> tuple[ResolvedScenario, ...]:
    """Resolve every applicable mesh edge without weakening requiredness."""

    if explicit and not candidates:
        raise ProviderContractError("requested scenario has no compatible mesh edge")
    if not candidates:
        return ()

    shared_issuer = planned_shared_issuer(profile)
    realm = shared_issuer.rsplit("/", 1)[-1]
    selected: list[ResolvedScenario] = []
    for candidate in candidates:
        if not candidate.cluster_ids or len(candidate.cluster_ids) != 2:
            raise ProviderContractError(
                "shared-IdP edge candidate must bind both endpoint clusters"
            )
        selected.append(
            ResolvedScenario(
                target_id=candidate.target_id,
                cluster_id=candidate.cluster_id,
                cluster_ids=candidate.cluster_ids,
                scenario_id=FEDERATION_SCENARIO_ID,
                required=candidate.required,
                case_ids=FEDERATION_CASE_IDS,
                redacted_parameters={
                    "issuer": shared_issuer,
                    "realm": realm,
                    "client_id": SHARED_REALM_CLIENT_ID,
                    "persona_usernames": [
                        "fed-clr-u",
                        "fed-clr-s",
                        "fed-clr-ts",
                        "fed-clr-unonboarded",
                        "fed-tenant-missing",
                        "fed-tenant-legacy-only",
                        "fed-tenant-nondefault",
                    ],
                    "fixture_mode": "owned",
                },
            )
        )
    return tuple(selected)


def planned_shared_issuer(profile: ValidationProfile) -> str:
    """Return the non-secret issuer URL that must be trusted before install.

    Kajiya supplies ``KAMIWAZA_SHARED_IDP_PUBLIC_URL`` from the deployment
    profile/environment.  A stable run hint is preferred when the orchestrator
    provides one; direct invocations fall back to the profile digest.  The
    result is deterministic for a fixed profile and environment and contains
    no credentials.
    """

    base = os.environ.get("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "").strip().rstrip("/")
    if not base:
        raise ProviderContractError(
            "KAMIWAZA_SHARED_IDP_PUBLIC_URL is required for shared-IdP resolution"
        )
    try:
        parsed = urlsplit(base)
    except ValueError:
        raise ProviderContractError("shared-IdP public URL is invalid") from None
    if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None:
        raise ProviderContractError(
            "shared-IdP public URL must be HTTPS and contain no userinfo"
        )
    # Kajiya sets this to the immutable run ID.  Hashing it avoids propagating
    # arbitrary characters into a Keycloak realm name while making concurrent
    # runs distinct even when they share one public Keycloak endpoint.
    run_hint = os.environ.get("KAMIWAZA_VALIDATION_RUN_ID", "").strip()
    suffix_source = run_hint or model_digest(profile)
    # Keep the derivation local and deterministic without exposing the run ID.
    import hashlib

    digest = hashlib.sha256(suffix_source.encode("utf-8")).hexdigest()[:16]
    realm = f"kz-validation-{digest}"
    return f"{base}/realms/{realm}"


def install_requirements(selected: Sequence[ResolvedScenario]) -> dict[str, Any]:
    """Publish issuer trust as a pre-install scheduler requirement."""

    issuers = {
        issuer
        for item in selected
        if isinstance(
            issuer := item.redacted_parameters.get("issuer"),
            str,
        )
    }
    return {"scheduler": {"trustedSharedIssuers": sorted(issuers)}} if issuers else {}
